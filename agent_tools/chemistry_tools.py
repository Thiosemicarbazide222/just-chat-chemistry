import requests
import eliot
import os
import sys

def _get_rdkit_chem():
    """
    Lazy import RDKit's Chem module. Raise ImportError with an actionable message if missing.
    """
    try:
        from rdkit import Chem  # type: ignore
        return Chem
    except Exception:
        raise ImportError(
            "RDKit is not installed. Install via conda (recommended): "
            "conda install -c conda-forge rdkit python=3.10"
        )
    
def get_ghs_classification(compound_input: str, input_type: str = "auto") -> dict:
    """
    Retrieve GHS classification from PubChem PUG-View, including hazard classes, categories,
    signal word, hazard statements (H-codes), and ALWAYS-INFERRED GHS pictograms.

    Args:
        compound_input: name, SMILES, or CID
        input_type: "auto" | "name" | "smiles" | "cid"

    Returns:
        dict with keys: cid, signal_word, hazard_classes, hazard_statements,
        pictograms, pictogram_markdown
    """
    import requests
    from urllib.parse import quote
    import re

    headers = {"User-Agent": "just-chat-chemistry-tools/1.0"}

    # ---------------------------
    # Resolve input → CID
    # ---------------------------
    try:
        if input_type == "auto":
            if re.match(r'^\d+$', compound_input):
                input_type = "cid"
            elif re.match(r'^[A-Za-z0-9()[\]{}@+\-=\\#%$:;.,]+$', compound_input) and any(
                c in compound_input for c in ['(', ')', '=', '#', '@']
            ):
                input_type = "smiles"
            else:
                input_type = "name"

        cid = None
        if input_type == "cid":
            cid = int(compound_input)

        elif input_type == "smiles":
            sm = quote(compound_input.strip())
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{sm}/cids/JSON"
            r = requests.get(url, timeout=15, headers=headers)
            r.raise_for_status()
            j = r.json()
            cid = j.get("IdentifierList", {}).get("CID", [None])[0]

        elif input_type == "name":
            nm = quote(compound_input.strip())
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{nm}/cids/JSON"
            r = requests.get(url, timeout=15, headers=headers)
            r.raise_for_status()
            j = r.json()

            if "IdentifierList" in j and "CID" in j["IdentifierList"]:
                cid = j["IdentifierList"]["CID"][0]
            elif "InformationList" in j and "Information" in j["InformationList"]:
                info = j["InformationList"]["Information"][0]
                if "CID" in info and info["CID"]:
                    cid = info["CID"][0]

        if not cid:
            return {"error": f"Could not resolve CID for input: {compound_input}"}

    except Exception as e:
        return {"error": f"Failed to resolve input to CID: {e}"}

    # ---------------------------
    # Fetch PubChem PUG-View JSON
    # ---------------------------
    try:
        view_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON/"
        resp = requests.get(view_url, timeout=30, headers=headers)
        resp.raise_for_status()
        view = resp.json()
    except Exception as e:
        return {"error": f"Failed to fetch PUG-View data: {e}"}

    # ---------------------------
    # Recursive section walker
    # ---------------------------
    def iterate_sections(node):
        if isinstance(node, dict):
            yield node
            for key in ("Section", "Sections", "Children"):
                if key in node and isinstance(node[key], list):
                    for child in node[key]:
                        yield from iterate_sections(child)
        elif isinstance(node, list):
            for item in node:
                yield from iterate_sections(item)

    # ---------------------------
    # Data extraction containers
    # ---------------------------
    signal_word = None
    hazard_classes = []
    hazard_statements = []

    record = (view or {}).get("Record", {})

    # ---------------------------
    # Extract hazard classes + H-codes
    # ---------------------------
    for sec in iterate_sections(record.get("Section", [])):
        heading = (sec.get("TOCHeading") or "").lower()

        if not any(h in heading for h in ["ghs", "hazard", "safety", "classification"]):
            continue

        for info in (sec.get("Information") or []):
            val = info.get("Value") or {}
            strings = [
                s.get("String")
                for s in (val.get("StringWithMarkup") or [])
                if isinstance(s, dict) and s.get("String")
            ]

            for s in strings:
                if not s:
                    continue

                # Signal word
                sl = s.lower()
                if ("signal word" in sl) and (":" in s):
                    signal_word = s.split(":", 1)[1].strip()

                # Hazard class (Category)
                m = re.search(r"(.*?)[\s]*-[\s]*Category\s*([0-9A-Za-z]+)", s)
                if m:
                    hazard_classes.append({
                        "class": m.group(1).strip(),
                        "category": m.group(2).strip()
                    })

                # H-code
                hm = re.search(r"\b(H\d{3}[A-Z]?)\b[: ]*(.*)", s)
                if hm:
                    hazard_statements.append({
                        "code": hm.group(1),
                        "text": hm.group(2).strip()
                    })

    # ---------------------------
    # ALWAYS-INFERRED PICTOGRAMS
    # ---------------------------
    code_to_fallback_image = {
        "GHS01": "https://upload.wikimedia.org/wikipedia/commons/6/6b/GHS-pictogram-explos.svg",
        "GHS02": "https://upload.wikimedia.org/wikipedia/commons/5/5a/GHS-pictogram-flamme.svg",
        "GHS03": "https://upload.wikimedia.org/wikipedia/commons/1/19/GHS-pictogram-comburant.svg",
        "GHS04": "https://upload.wikimedia.org/wikipedia/commons/c/cf/GHS-pictogram-bouteille_a_gaz.svg",
        "GHS05": "https://upload.wikimedia.org/wikipedia/commons/8/80/GHS-pictogram-corrosion.svg",
        "GHS06": "https://upload.wikimedia.org/wikipedia/commons/9/90/GHS-pictogram-tete-de-mort.svg",
        "GHS07": "https://upload.wikimedia.org/wikipedia/commons/3/3b/GHS-pictogram-exclamation.svg",
        "GHS08": "https://upload.wikimedia.org/wikipedia/commons/2/26/GHS-pictogram-silhouette.svg",
        "GHS09": "https://upload.wikimedia.org/wikipedia/commons/f/f7/GHS-pictogram-environnement.svg",
    }

    hcodes = {h["code"] for h in hazard_statements}

    inferred_pictos = set()

    # Explosive
    if any(h.startswith("H20") for h in hcodes):
        inferred_pictos.add("GHS01")

    # Acute toxicity
    if any(h.startswith(x) for x in ("H30", "H31", "H33") for h in hcodes):
        inferred_pictos.add("GHS06")

    # Environmental hazard
    if any(h.startswith("H40") or h.startswith("H41") for h in hcodes):
        inferred_pictos.add("GHS09")

    # STOT / carcinogenicity / reproductive toxicity
    if any(h.startswith("H36") or h.startswith("H37") or h.startswith("H38") for h in hcodes):
        inferred_pictos.add("GHS08")

    # Skin/eye irritation pictogram
    if any(x in hcodes for x in ("H315", "H319", "H335")):
        inferred_pictos.add("GHS07")

    pictograms = [
        {"code": code, "image_url": code_to_fallback_image.get(code)}
        for code in sorted(inferred_pictos)
    ]

    pictogram_markdown = [
        f"![{p['code']}]({p['image_url']})"
        for p in pictograms
    ]

    # ---------------------------
    # Final result
    # ---------------------------
    return {
        "cid": cid,
        "signal_word": signal_word,
        "hazard_classes": hazard_classes,
        "hazard_statements": hazard_statements,
        "pictograms": pictograms,
        "pictogram_markdown": pictogram_markdown
    }

def check_chemical_weapon_potential(compound_input: str, input_type: str = "auto") -> dict:
    """
    Determine whether PubChem describes the substance as a chemical weapon or warfare agent.
    Uses PubChem PUG-View sections to search for Chemical Weapons Convention (CWC) schedules,
    warfare agent designations (nerve, blister, choking, riot control, etc.), and related labels.

    Args:
        compound_input: Compound name, SMILES, or CID.
        input_type: "auto" | "name" | "smiles" | "cid"

    Returns:
        dict with keys:
            - cid
            - is_potential_chemical_weapon (bool)
            - confidence ("high" | "medium" | "low" | "unknown")
            - detected_keywords (list[str])
            - evidence (list[{section, text, keyword, confidence}])
            - note (str, optional)
        or {"error": "..."}
    """
    from urllib.parse import quote
    import re

    if not compound_input or not compound_input.strip():
        return {"error": "No compound input provided."}

    headers = {"User-Agent": "just-chat-chemistry-tools/1.0"}

    # Resolve input to CID
    try:
        if input_type == "auto":
            stripped = compound_input.strip()
            if re.match(r"^\d+$", stripped):
                input_type = "cid"
            elif re.match(r"^[A-Za-z0-9()[\]{}@+\-=\\#%$:;.,]+$", stripped) and any(
                c in stripped for c in ["(", ")", "=", "#", "@", "[", "]"]
            ):
                input_type = "smiles"
            else:
                input_type = "name"

        cid = None
        if input_type == "cid":
            cid = int(compound_input)
        elif input_type == "smiles":
            sm = quote(compound_input.strip())
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{sm}/cids/JSON"
            resp = requests.get(url, timeout=15, headers=headers)
            resp.raise_for_status()
            j = resp.json()
            cid = j.get("IdentifierList", {}).get("CID", [None])[0]
        elif input_type == "name":
            nm = quote(compound_input.strip())
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{nm}/cids/JSON"
            resp = requests.get(url, timeout=15, headers=headers)
            resp.raise_for_status()
            j = resp.json()
            if "IdentifierList" in j and "CID" in j["IdentifierList"]:
                cid = j["IdentifierList"]["CID"][0]
            elif "InformationList" in j and "Information" in j["InformationList"]:
                info = j["InformationList"]["Information"][0]
                if "CID" in info and info["CID"]:
                    cid = info["CID"][0]
        if not cid:
            return {"error": f"Could not resolve CID for input: {compound_input}"}
    except Exception as exc:
        return {"error": f"Failed to resolve input to CID: {exc}"}

    # Fetch PUG-View data
    try:
        view_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON/"
        resp = requests.get(view_url, timeout=30, headers=headers)
        resp.raise_for_status()
        view = resp.json()
    except Exception as exc:
        return {"error": f"Failed to fetch PubChem PUG-View data: {exc}"}

    def iterate_sections(node):
        if isinstance(node, dict):
            yield node
            for key in ("Section", "Children", "Sections"):
                if key in node and isinstance(node[key], list):
                    for child in node[key]:
                        yield from iterate_sections(child)
        elif isinstance(node, list):
            for item in node:
                yield from iterate_sections(item)

    def extract_strings(info: dict) -> list[str]:
        texts: list[str] = []
        value = info.get("Value") or {}
        strings = value.get("StringWithMarkup") or []
        for entry in strings:
            text = entry.get("String")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        desc = info.get("Description")
        if isinstance(desc, str) and desc.strip():
            texts.append(desc.strip())
        name = info.get("Name")
        if isinstance(name, str) and name.strip():
            texts.append(name.strip())
        return texts

    keyword_rules = [
        {"pattern": r"\bchemical weapon", "confidence": "high", "label": "Explicit 'chemical weapon' mention"},
        {"pattern": r"\bchemical warfare agent", "confidence": "high", "label": "Chemical warfare agent"},
        {"pattern": r"\bcwc\b", "confidence": "medium", "label": "Chemical Weapons Convention reference"},
        {"pattern": r"\bschedule\s*1\b", "confidence": "high", "label": "CWC Schedule 1"},
        {"pattern": r"\bschedule\s*2\b", "confidence": "medium", "label": "CWC Schedule 2"},
        {"pattern": r"\bschedule\s*3\b", "confidence": "medium", "label": "CWC Schedule 3"},
        {"pattern": r"\bnerve agent", "confidence": "high", "label": "Nerve agent classification"},
        {"pattern": r"\bblister agent|\bvesicant", "confidence": "high", "label": "Blister/Vesicant agent"},
        {"pattern": r"\bchoking agent|\bpulmonary agent", "confidence": "medium", "label": "Choking/Pulmonary agent"},
        {"pattern": r"\briot control agent|\blachrymator|\blachrymatory", "confidence": "medium", "label": "Riot-control agent"},
        {"pattern": r"\bincapacitating agent", "confidence": "medium", "label": "Incapacitating agent"},
        {"pattern": r"\bblood agent", "confidence": "medium", "label": "Blood agent"},
        {"pattern": r"\bcombat\b.+\bagent", "confidence": "medium", "label": "Combat agent reference"},
    ]
    confidence_rank = {"low": 1, "medium": 2, "high": 3}

    evidence = []
    detected_keywords = set()
    best_confidence = "unknown"
    best_rank = 0
    dedupe_hits = set()

    record = (view or {}).get("Record", {})
    for section in iterate_sections(record.get("Section", [])):
        heading = section.get("TOCHeading") or "Unknown section"
        infos = section.get("Information") or []
        for info in infos:
            for text in extract_strings(info):
                lower_text = text.lower()
                for rule in keyword_rules:
                    if re.search(rule["pattern"], lower_text, flags=re.IGNORECASE):
                        key = (text, rule["label"])
                        if key in dedupe_hits:
                            continue
                        dedupe_hits.add(key)
                        evidence.append(
                            {
                                "section": heading,
                                "text": text,
                                "keyword": rule["label"],
                                "confidence": rule["confidence"],
                            }
                        )
                        detected_keywords.add(rule["label"])
                        rank = confidence_rank.get(rule["confidence"], 0)
                        if rank > best_rank:
                            best_rank = rank
                            best_confidence = rule["confidence"]

    result = {
        "cid": cid,
        "is_potential_chemical_weapon": bool(evidence),
        "confidence": best_confidence,
        "detected_keywords": sorted(detected_keywords),
        "evidence": evidence,
        "source": "PubChem PUG-View",
    }
    if not evidence:
        result["note"] = "No chemical weapon designations found in PubChem records."
    return result

def get_ld50(compound_input: str, input_type: str = "auto") -> dict:
    """
    Retrieve LD50 toxicity data for a compound using PubChem PUG-View.

    Args:
        compound_input: name, SMILES, or CID
        input_type: one of "auto", "name", "smiles", "cid"

    Returns:
        dict with keys:
          - cid: int
          - ld50_entries: list of parsed LD50 items (species, route, value, units, note, source)
          - raw_count: number of LD50 mentions found
        or {"error": ...}
    """
    from urllib.parse import quote
    import re

    headers = {"User-Agent": "just-chat-chemistry-tools/1.0"}

    # Resolve input to CID
    try:
        if input_type == "auto":
            if re.match(r'^\d+$', compound_input):
                input_type = "cid"
            elif re.match(r'^[A-Za-z0-9()[\]{}@+\-=\\#%$:;.,]+$', compound_input) and any(c in compound_input for c in ['(', ')', '=', '#', '@']):
                input_type = "smiles"
            else:
                input_type = "name"

        cid = None
        if input_type == "cid":
            cid = int(compound_input)
        elif input_type == "smiles":
            sm = quote(compound_input.strip())
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{sm}/cids/JSON"
            r = requests.get(url, timeout=15, headers=headers)
            r.raise_for_status()
            j = r.json()
            cid = j.get("IdentifierList", {}).get("CID", [None])[0]
        elif input_type == "name":
            nm = quote(compound_input.strip())
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{nm}/cids/JSON"
            r = requests.get(url, timeout=15, headers=headers)
            r.raise_for_status()
            j = r.json()
            if "IdentifierList" in j and "CID" in j["IdentifierList"]:
                cid = j["IdentifierList"]["CID"][0]
            elif "InformationList" in j and "Information" in j["InformationList"]:
                info = j["InformationList"]["Information"][0]
                if "CID" in info and info["CID"]:
                    cid = info["CID"][0]
        if not cid:
            return {"error": f"Could not resolve CID for input: {compound_input}"}
    except Exception as e:
        return {"error": f"Failed to resolve input to CID: {e}"}

    # Fetch PUG-View toxicity sections
    try:
        view_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON/"
        resp = requests.get(view_url, timeout=30, headers=headers)
        resp.raise_for_status()
        view = resp.json()
    except Exception as e:
        return {"error": f"Failed to fetch PUG-View data: {e}"}

    # Helpers to traverse and extract LD50 strings
    def collect_sections(node):
        sections = []
        if isinstance(node, dict):
            if node.get("TOCHeading") or node.get("TOCHeading", ""):
                sections.append(node)
            # Recurse into children lists
            for key in ("Section", "Children", "Sections"):
                if key in node and isinstance(node[key], list):
                    for child in node[key]:
                        sections.extend(collect_sections(child))
        elif isinstance(node, list):
            for item in node:
                sections.extend(collect_sections(item))
        return sections

    def extract_information_strings(section):
        texts = []
        infos = section.get("Information", []) if isinstance(section, dict) else []
        for info in infos:
            val = info.get("Value") if isinstance(info, dict) else None
            if not val:
                continue
            # StringWithMarkup entries
            for swm in val.get("StringWithMarkup", []) or []:
                s = swm.get("String")
                if s:
                    texts.append({
                        "text": s,
                        "reference": (info.get("Reference", [{}])[0].get("Name") if info.get("Reference") else None)
                    })
        return texts

    # Identify toxicity-related sections
    record = (view or {}).get("Record", {})
    top_sections = record.get("Section", [])
    all_sections = collect_sections(top_sections)

    candidate_sections = []
    for sec in all_sections:
        heading = (sec.get("TOCHeading") or "").lower()
        if any(h in heading for h in ["toxicity", "toxicological", "safety", "hazards"]):
            candidate_sections.append(sec)

    # Extract LD50 mentions
    ld50_texts = []
    for sec in candidate_sections:
        ld50_texts.extend(extract_information_strings(sec))

    # Filter for LD50; parse simple patterns like "LD50 Oral rat: 200 mg/kg"
    ld50_entries = []
    ld50_pattern = re.compile(r"LD50\s*([^:;\n]*)[:;,-]?\s*([\d,.]+)\s*(mg/kg|g/kg|ug/kg|µg/kg)", re.IGNORECASE)
    for item in ld50_texts:
        text = item["text"]
        if "ld50" not in text.lower():
            continue
        # Try to parse one or more values from the text
        for match in ld50_pattern.finditer(text):
            context = match.group(1).strip() if match.group(1) else ""
            value_str = match.group(2).replace(",", "")
            units = match.group(3)
            try:
                value = float(value_str)
            except Exception:
                value = None
            # Heuristics for route/species from context fragment
            route = None
            species = None
            ctx_lower = context.lower()
            for r in ["oral", "dermal", "intraperitoneal", "intravenous", "inhalation", "subcutaneous"]:
                if r in ctx_lower:
                    route = r
                    break
            for sp in ["rat", "mouse", "mice", "rabbit", "guinea pig", "dog", "human"]:
                if sp in ctx_lower:
                    species = sp
                    break
            ld50_entries.append({
                "text": text,
                "value": value,
                "units": units,
                "route": route,
                "species": species,
                "source": item.get("reference")
            })

        # If no numeric parse, but contains LD50, include as raw
        if not any(m.group(0) for m in ld50_pattern.finditer(text)):
            ld50_entries.append({
                "text": text,
                "value": None,
                "units": None,
                "route": None,
                "species": None,
                "source": item.get("reference")
            })

    # Deduplicate by text
    seen = set()
    unique_entries = []
    for e in ld50_entries:
        key = e["text"]
        if key in seen:
            continue
        seen.add(key)
        unique_entries.append(e)

    return {
        "cid": cid,
        "raw_count": len(ld50_entries),
        "ld50_entries": unique_entries
    }

def get_fda_approval(compound_input: str, input_type: str = "auto") -> dict:
    """
    Retrieve FDA approval information for a compound via PubChem PUG-View.

    Args:
        compound_input: name, SMILES, or CID
        input_type: one of "auto", "name", "smiles", "cid"

    Returns:
        dict with keys:
          - cid: int
          - approved: bool | None
          - approval_years: list[int]
          - application_numbers: list[str]  # NDA/ANDA/BLA identifiers
          - marketing_status: list[str]
          - evidence: list[ {source, section, text} ]
        or {"error": ...}
    """
    from urllib.parse import quote
    import re

    headers = {"User-Agent": "just-chat-chemistry-tools/1.0"}

    # Resolve input to CID
    try:
        if input_type == "auto":
            if re.match(r'^\d+$', compound_input):
                input_type = "cid"
            elif re.match(r'^[A-Za-z0-9()[\]{}@+\-=\\#%$:;.,]+$', compound_input) and any(c in compound_input for c in ['(', ')', '=', '#', '@']):
                input_type = "smiles"
            else:
                input_type = "name"

        cid = None
        if input_type == "cid":
            cid = int(compound_input)
        elif input_type == "smiles":
            sm = quote(compound_input.strip())
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{sm}/cids/JSON"
            r = requests.get(url, timeout=15, headers=headers)
            r.raise_for_status()
            j = r.json()
            cid = j.get("IdentifierList", {}).get("CID", [None])[0]
        elif input_type == "name":
            nm = quote(compound_input.strip())
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{nm}/cids/JSON"
            r = requests.get(url, timeout=15, headers=headers)
            r.raise_for_status()
            j = r.json()
            if "IdentifierList" in j and "CID" in j["IdentifierList"]:
                cid = j["IdentifierList"]["CID"][0]
            elif "InformationList" in j and "Information" in j["InformationList"]:
                info = j["InformationList"]["Information"][0]
                if "CID" in info and info["CID"]:
                    cid = info["CID"][0]
        if not cid:
            return {"error": f"Could not resolve CID for input: {compound_input}"}
    except Exception as e:
        return {"error": f"Failed to resolve input to CID: {e}"}

    # Fetch PUG-View JSON
    try:
        view_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON/"
        resp = requests.get(view_url, timeout=30, headers=headers)
        resp.raise_for_status()
        view = resp.json()
    except Exception as e:
        return {"error": f"Failed to fetch PUG-View data: {e}"}

    # Traverse all sections recursively
    def iterate_sections(node):
        if isinstance(node, dict):
            yield node
            for key in ("Section", "Children", "Sections"):
                if key in node and isinstance(node[key], list):
                    for child in node[key]:
                        yield from iterate_sections(child)
        elif isinstance(node, list):
            for item in node:
                yield from iterate_sections(item)

    # Collect FDA-related evidence
    evidence = []
    approval_years: list[int] = []
    application_numbers: list[str] = []
    marketing_status: list[str] = []
    approved_signals: int = 0
    withdrawn_signals: int = 0

    record = (view or {}).get("Record", {})
    for sec in iterate_sections(record.get("Section", [])):
        heading = (sec.get("TOCHeading") or "")
        heading_l = heading.lower()
        looks_relevant = any(k in heading_l for k in [
            "fda", "orange book", "drug and medication", "regulatory status", "approval", "drugbank"
        ])
        if not looks_relevant:
            continue

        infos = sec.get("Information", []) or []
        for info in infos:
            val = info.get("Value") or {}
            strings = [s.get("String") for s in (val.get("StringWithMarkup") or []) if s.get("String")]
            # Include Description as a string if present
            desc = info.get("Description")
            if isinstance(desc, str) and desc:
                strings.append(desc)

            # Extract signals and fields
            for s in strings:
                if not isinstance(s, str):
                    continue
                s_clean = s.strip()
                s_l = s_clean.lower()
                # Approval signals
                if re.search(r"\b(fda[- ]?approved|approved by fda|us fda approved)\b", s_l):
                    approved_signals += 1
                if re.search(r"\bwithdrawn\b", s_l):
                    withdrawn_signals += 1
                # Years
                for ym in re.finditer(r"\b(19|20)\d{2}\b", s_clean):
                    year = int(ym.group(0))
                    if year not in approval_years:
                        approval_years.append(year)
                # Application numbers NDA/ANDA/BLA
                for am in re.finditer(r"\b(NDA|ANDA|BLA)\s*\d+\b", s_clean, flags=re.IGNORECASE):
                    app = am.group(0).upper()
                    if app not in application_numbers:
                        application_numbers.append(app)
                # Marketing status
                for ms in ["prescription", "otc", "over the counter", "discontinued", "rx-only", "investigational"]:
                    if ms in s_l and s_clean not in marketing_status:
                        marketing_status.append(s_clean)

                evidence.append({
                    "source": (info.get("Reference", [{}])[0].get("Name") if info.get("Reference") else None),
                    "section": heading,
                    "text": s_clean,
                })

    approved: bool | None
    if approved_signals > 0 and withdrawn_signals == 0:
        approved = True
    elif withdrawn_signals > 0 and approved_signals == 0:
        approved = False
    elif approved_signals == 0 and withdrawn_signals == 0:
        # Look for generic cues like DrugBank status lines
        status_lines = [e["text"].lower() for e in evidence]
        if any("status" in t and "approved" in t for t in status_lines):
            approved = True
        elif any("status" in t and "withdrawn" in t for t in status_lines):
            approved = False
        else:
            approved = None
    else:
        # Conflicting signals
        approved = None

    return {
        "cid": cid,
        "approved": approved,
        "approval_years": sorted(approval_years),
        "application_numbers": application_numbers,
        "marketing_status": marketing_status,
        "evidence": evidence,
    }

def smiles_to_molecular_weight(smiles: str):
    """
    Compute/lookup the compound molecular weight from a SMILES string via PubChem.

    Returns:
        - float (molecular weight in g/mol) on success
        - dict with "error" key on failure
    """
    from urllib.parse import quote

    if not smiles or not smiles.strip():
        return {"error": "No SMILES string provided."}

    headers = {"User-Agent": "just-chat-chemistry-tools/1.0"}

    # Average atomic weights (IUPAC standard atomic weights; truncated set covering common elements)
    ATOMIC_WEIGHTS = {
        "H": 1.00794,
        "He": 4.002602,
        "Li": 6.941,
        "Be": 9.012182,
        "B": 10.811,
        "C": 12.0107,
        "N": 14.0067,
        "O": 15.9994,
        "F": 18.9984032,
        "Ne": 20.1797,
        "Na": 22.98976928,
        "Mg": 24.3050,
        "Al": 26.9815386,
        "Si": 28.0855,
        "P": 30.973762,
        "S": 32.065,
        "Cl": 35.453,
        "Ar": 39.948,
        "K": 39.0983,
        "Ca": 40.078,
        "Sc": 44.955912,
        "Ti": 47.867,
        "V": 50.9415,
        "Cr": 51.9961,
        "Mn": 54.938045,
        "Fe": 55.845,
        "Co": 58.933195,
        "Ni": 58.6934,
        "Cu": 63.546,
        "Zn": 65.38,
        "Ga": 69.723,
        "Ge": 72.64,
        "As": 74.92160,
        "Se": 78.96,
        "Br": 79.904,
        "Kr": 83.798,
        "Rb": 85.4678,
        "Sr": 87.62,
        "Y": 88.90585,
        "Zr": 91.224,
        "Nb": 92.90638,
        "Mo": 95.96,
        "Tc": 98.0,
        "Ru": 101.07,
        "Rh": 102.90550,
        "Pd": 106.42,
        "Ag": 107.8682,
        "Cd": 112.411,
        "In": 114.818,
        "Sn": 118.710,
        "Sb": 121.760,
        "Te": 127.60,
        "I": 126.90447,
        "Xe": 131.293,
        "Cs": 132.9054519,
        "Ba": 137.327,
        "La": 138.90547,
        "Ce": 140.116,
        "Pr": 140.90765,
        "Nd": 144.242,
        "Sm": 150.36,
        "Eu": 151.964,
        "Gd": 157.25,
        "Tb": 158.92535,
        "Dy": 162.500,
        "Ho": 164.93032,
        "Er": 167.259,
        "Tm": 168.93421,
        "Yb": 173.054,
        "Lu": 174.9668,
        "Hf": 178.49,
        "Ta": 180.94788,
        "W": 183.84,
        "Re": 186.207,
        "Os": 190.23,
        "Ir": 192.217,
        "Pt": 195.084,
        "Au": 196.966569,
        "Hg": 200.59,
        "Tl": 204.3833,
        "Pb": 207.2,
        "Bi": 208.98040,
        "Po": 209.0,
        "At": 210.0,
        "Rn": 222.0,
    }

    def parse_formula(formula: str) -> dict:
        """Parse a chemical formula into element counts.
        Supports parentheses and hydrate separators ('.' or '·').
        """
        import re

        def merge_counts(target: dict, source: dict, factor: int = 1) -> None:
            for k, v in source.items():
                target[k] = target.get(k, 0) + v * factor

        def parse_segment(seg: str, idx: int = 0) -> tuple[dict, int]:
            counts: dict[str, int] = {}
            n = len(seg)
            while idx < n:
                ch = seg[idx]
                if ch == '(':
                    inner, new_idx = parse_segment(seg, idx + 1)
                    idx = new_idx
                    # read multiplier
                    m = re.match(r"(\d+)", seg[idx:])
                    mult = int(m.group(1)) if m else 1
                    if m:
                        idx += len(m.group(1))
                    merge_counts(counts, inner, mult)
                    continue
                if ch == ')':
                    return counts, idx + 1
                if ch == '[':
                    # Handle bracketed groups similarly to parentheses
                    inner, new_idx = parse_segment(seg, idx + 1)
                    idx = new_idx
                    m = re.match(r"(\d+)", seg[idx:])
                    mult = int(m.group(1)) if m else 1
                    if m:
                        idx += len(m.group(1))
                    merge_counts(counts, inner, mult)
                    continue
                if ch == ']':
                    return counts, idx + 1
                if ch == '.' or ch == '·':
                    idx += 1
                    continue
                # Element symbol
                m = re.match(r"([A-Z][a-z]?)", seg[idx:])
                if not m:
                    # skip any other tokens like charges
                    idx += 1
                    continue
                elem = m.group(1)
                idx += len(elem)
                m2 = re.match(r"(\d+)", seg[idx:])
                count = int(m2.group(1)) if m2 else 1
                if m2:
                    idx += len(m2.group(1))
                counts[elem] = counts.get(elem, 0) + count
            return counts, idx

        # Handle hydrates or dot-separated parts: sum them
        total: dict[str, int] = {}
        # Split on '.' and '·'
        parts = re.split(r"[\.·]", formula)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # Possible leading multiplier like '5H2O'
            mlead = re.match(r"^(\d+)(.*)$", part)
            lead_mult = 1
            seg = part
            if mlead:
                lead_mult = int(mlead.group(1))
                seg = mlead.group(2)
            counts, _ = parse_segment(seg, 0)
            merge_counts(total, counts, lead_mult)
        return total

    def compute_weight_from_counts(counts: dict) -> float:
        total = 0.0
        for elem, cnt in counts.items():
            if elem not in ATOMIC_WEIGHTS:
                raise ValueError(f"Unknown element in formula: {elem}")
            total += ATOMIC_WEIGHTS[elem] * cnt
        return total

    try:
        encoded_smiles = quote(smiles.strip())
        # Retrieve molecular formula from PubChem for the SMILES
        url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{encoded_smiles}/"
            "property/MolecularFormula/JSON"
        )
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status()
        data = response.json()
        props = data.get("PropertyTable", {}).get("Properties", [])
        if not props:
            return {"error": "No property data found in PubChem response"}
        formula = props[0].get("MolecularFormula")
        if not formula:
            return {"error": "MolecularFormula not found in PubChem response"}
        counts = parse_formula(formula)
        weight = compute_weight_from_counts(counts)
        return float(weight)
    except Exception as exc:
        return {"error": f"Failed to compute molecular weight from formula: {exc}"}

def smiles_to_name(smiles: str) -> str:
    """
    Given a SMILES string, query PubChem and return the compound's name.
    Always returns the best match for the SMILES string.
    """
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{smiles}/property/IUPACName,Title/JSON"
    response = requests.get(url, timeout=10)
    if response.status_code != 200:
        return f"PubChem lookup failed (status {response.status_code})."
    data = response.json()
    try:
        props = data["PropertyTable"]["Properties"][0]
        # Prefer Title (common name), fallback to IUPACName
        return props.get("Title") or props.get("IUPACName") or "Name not found in PubChem."
    except Exception:
        return "Could not parse PubChem response."

def name_to_smiles(name: str) -> str:
    """
    Resolve a chemical name to its SMILES using PubChem.

    Strategy:
    - Try direct property-by-name (multiple SMILES types)
    - Try CID lookup then fetch SMILES for the first CID
    - Accept any available SMILES type (Canonical, Isomeric, Connectivity)

    Returns the SMILES string on success, or an error string on failure.
    """
    from urllib.parse import quote

    if not name or not name.strip():
        return "Error: No compound name provided."

    encoded_name = quote(name.strip())
    headers = {"User-Agent": "just-chat-chemistry-tools/1.0"}

    # Helper: extract first available SMILES from property response
    def extract_smiles(entry):
        for smiles_type in ["CanonicalSMILES", "IsomericSMILES", "ConnectivitySMILES"]:
            if entry.get(smiles_type):
                return entry[smiles_type]
        return None

    # 1) Direct property-by-name (try all SMILES types)
    try:
        direct_url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded_name}/"
            "property/CanonicalSMILES,IsomericSMILES,ConnectivitySMILES/JSON"
        )
        direct_resp = requests.get(direct_url, timeout=10, headers=headers)
        direct_resp.raise_for_status()
        dj = direct_resp.json()
        entry = dj["PropertyTable"]["Properties"][0]
        smiles = extract_smiles(entry)
        if smiles:
            return smiles
    except Exception:
        pass

    # Helper: fetch first CID via name->cids
    def fetch_first_cid(n: str) -> int | None:
        try:
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{n}/cids/JSON"
            r = requests.get(url, timeout=10, headers=headers)
            r.raise_for_status()
            data = r.json()
            if "IdentifierList" in data and "CID" in data["IdentifierList"]:
                cids = data["IdentifierList"]["CID"]
                return cids[0] if cids else None
            if "InformationList" in data and "Information" in data["InformationList"]:
                info = data["InformationList"]["Information"][0]
                cid_field = info.get("CID")
                if isinstance(cid_field, list) and cid_field:
                    return cid_field[0]
                if isinstance(cid_field, int):
                    return cid_field
            return None
        except Exception:
            return None

    # 2) CID lookup then fetch SMILES
    cid = fetch_first_cid(encoded_name)
    if cid is not None:
        try:
            prop_url = (
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/"
                "property/CanonicalSMILES,IsomericSMILES,ConnectivitySMILES/JSON"
            )
            prop_resp = requests.get(prop_url, timeout=10, headers=headers)
            prop_resp.raise_for_status()
            pj = prop_resp.json()
            pentry = pj["PropertyTable"]["Properties"][0]
            smiles = extract_smiles(pentry)
            if smiles:
                return smiles
        except Exception:
            pass

    return "SMILES not found in PubChem."

def get_physical_properties(compound_input: str, input_type: str = "auto") -> dict:
    """
    Retrieve comprehensive physical properties of a compound from PubChem.
    
    Args:
        compound_input: The compound identifier (name, SMILES, or CID)
        input_type: Type of input - "name", "smiles", "cid", or "auto" (default, tries to detect)
    
    Returns:
        Dictionary containing all available physical properties from PubChem
    """
    from urllib.parse import quote
    import re
    
    headers = {"User-Agent": "just-chat-chemistry-tools/1.0"}
    
    # Auto-detect input type if not specified
    if input_type == "auto":
        if re.match(r'^\d+$', compound_input):
            input_type = "cid"
        elif re.match(r'^[A-Za-z0-9()[\]{}@+\-=\\#%$:;.,]+$', compound_input) and any(c in compound_input for c in ['(', ')', '=', '#', '@']):
            input_type = "smiles"
        else:
            input_type = "name"
    
    # Get CID first if needed
    cid = None
    if input_type == "cid":
        cid = int(compound_input)
    elif input_type == "smiles":
        # Convert SMILES to CID
        try:
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{compound_input}/cids/JSON"
            response = requests.get(url, timeout=10, headers=headers)
            response.raise_for_status()
            data = response.json()
            if "IdentifierList" in data and "CID" in data["IdentifierList"]:
                cid = data["IdentifierList"]["CID"][0]
        except Exception as e:
            return {"error": f"Failed to convert SMILES to CID: {e}"}
    elif input_type == "name":
        # Convert name to CID
        try:
            encoded_name = quote(compound_input.strip())
            url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded_name}/cids/JSON"
            response = requests.get(url, timeout=10, headers=headers)
            response.raise_for_status()
            data = response.json()
            if "IdentifierList" in data and "CID" in data["IdentifierList"]:
                cid = data["IdentifierList"]["CID"][0]
            elif "InformationList" in data and "Information" in data["InformationList"]:
                info = data["InformationList"]["Information"][0]
                if "CID" in info and info["CID"]:
                    cid = info["CID"][0]
        except Exception as e:
            return {"error": f"Failed to convert name to CID: {e}"}
    
    if not cid:
        return {"error": f"Could not find CID for compound: {compound_input}"}
    
    # Use properties that are actually available in PubChem API
    available_properties = [
        "IUPACName", "Title", "SMILES", "InChI", "InChIKey",
        "MolecularFormula", "MolecularWeight", "XLogP", "TPSA", "Complexity", "Charge",
        "HBondDonorCount", "HBondAcceptorCount", "RotatableBondCount", "HeavyAtomCount",
        "ExactMass", "MonoisotopicMass"
    ]
    
    try:
        # Make a single request with the available properties
        properties_str = ",".join(available_properties)
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/{properties_str}/JSON"
        response = requests.get(url, timeout=15, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if "PropertyTable" in data and "Properties" in data["PropertyTable"]:
            props = data["PropertyTable"]["Properties"][0]
            
            # Organize properties into categories
            result = {
                "cid": cid,
                "compound_info": {},
                "molecular_properties": {},
                "spectral_properties": {},
                "other_properties": {}
            }
            
            # Categorize properties
            compound_info_keys = ["IUPACName", "Title", "SMILES", "InChI", "InChIKey"]
            molecular_keys = ["MolecularFormula", "MolecularWeight", "XLogP", "TPSA", "Complexity", "Charge", 
                            "HBondDonorCount", "HBondAcceptorCount", "RotatableBondCount", "HeavyAtomCount"]
            spectral_keys = ["ExactMass", "MonoisotopicMass"]
            
            # Organize properties into categories
            for key, value in props.items():
                if value is not None:
                    if key in compound_info_keys:
                        result["compound_info"][key] = value
                    elif key in molecular_keys:
                        result["molecular_properties"][key] = value
                    elif key in spectral_keys:
                        result["spectral_properties"][key] = value
                    else:
                        result["other_properties"][key] = value
            
            # Remove empty categories
            result = {k: v for k, v in result.items() if v}
            
            return result
        else:
            return {"error": "No property data found in PubChem response"}
            
    except Exception as e:
        return {"error": f"Failed to retrieve physical properties: {e}"}

def search_compound_best_match(search_term: str) -> dict: 
    """
    Search for a compound by name and return the best match with comprehensive information.
    Returns a dictionary with CID, name, SMILES, and other properties.
    """
    try:
        # First try exact name match
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{search_term}/property/CID,IUPACName,Title,CanonicalSMILES,MolecularFormula,MolecularWeight/JSON"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        props = data["PropertyTable"]["Properties"][0]
        return {
            "cid": props.get("CID"),
            "name": props.get("Title") or props.get("IUPACName"),
            "smiles": props.get("CanonicalSMILES"),
            "formula": props.get("MolecularFormula"),
            "weight": props.get("MolecularWeight"),
            "match_type": "exact"
        }
    except Exception:
            # If exact match fails, try fuzzy search
            try:
                search_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{search_term}/cids/JSON"
                search_response = requests.get(search_url, timeout=10)
                search_response.raise_for_status()
                search_data = search_response.json()
                
                # Check if we have results
                if "InformationList" in search_data and "Information" in search_data["InformationList"]:
                    info = search_data["InformationList"]["Information"][0]
                    if "CID" in info and info["CID"]:
                        # Get comprehensive info for the best match
                        best_cid = info["CID"][0]
                        cid_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{best_cid}/property/IUPACName,Title,CanonicalSMILES,MolecularFormula,MolecularWeight/JSON"
                        cid_response = requests.get(cid_url, timeout=10)
                        cid_response.raise_for_status()
                        cid_data = cid_response.json()
                        cid_props = cid_data["PropertyTable"]["Properties"][0]
                        return {
                            "cid": cid_props.get("CID"),
                            "name": cid_props.get("Title") or cid_props.get("IUPACName"),
                            "smiles": cid_props.get("CanonicalSMILES"),
                            "formula": cid_props.get("MolecularFormula"),
                            "weight": cid_props.get("MolecularWeight"),
                            "match_type": "fuzzy"
                        }
                
                return {"error": f"No matches found for '{search_term}' in PubChem."}
            except Exception as e:
                return {"error": f"Error in PubChem lookup: {e}"}

def identify_functional_groups(smiles: str = None, name: str = None) -> dict:
    """
    Identify functional groups in a molecule using IFG, given a SMILES string or a molecule name.
    If name is provided, it is converted to SMILES using name_to_smiles().
    Returns a dictionary of functional groups and their counts.
    """
    # Lazy-import IFG and try to auto-resolve its path if missing
    def _import_molecule():
        try:
            from chem.molecule import Molecule  # type: ignore
            return Molecule
        except Exception as import_exc:
            # Try to locate IFG via env var or common local paths
            candidate_paths = []
            # Support both IFG_PATH (points to 'ifg' folder) and IFG (repo root or 'ifg' folder)
            ifg_env = os.environ.get("IFG_PATH") or os.environ.get("IFG")
            if ifg_env:
                candidate_paths.append(ifg_env)
                candidate_paths.append(os.path.join(ifg_env, "ifg"))
            # Common relative locations (repo root or /app inside container)
            candidate_paths.extend([
                os.path.join(os.getcwd(), "external", "IFG", "ifg"),
                os.path.join(os.path.dirname(__file__), "..", "external", "IFG", "ifg"),
                os.path.join(os.getcwd(), "..", "IFG", "ifg"),
                "/app/external/IFG/ifg",
            ])
            for path in candidate_paths:
                try:
                    norm_path = os.path.abspath(path)
                    if os.path.isdir(norm_path) and norm_path not in sys.path:
                        sys.path.insert(0, norm_path)
                        from chem.molecule import Molecule  # type: ignore
                        return Molecule
                except Exception:
                    continue
            # If still failing, surface a helpful error
            raise ImportError(
                "IFG not found. Set IFG_PATH or IFG to the 'ifg' folder (or repo root) from "
                "https://github.com/wtriddle/IFG, or place it at ../IFG/ifg or ./external/IFG/ifg."
            ) from import_exc

    Molecule = _import_molecule()
    with eliot.start_action(action_type="identify_functional_groups", smiles=smiles, name=name):
        if name and not smiles:
            smiles = name_to_smiles(name)
            if not smiles or smiles.startswith("Error"):
                return {"error": f"Could not resolve name '{name}' to SMILES."}
        if not smiles:
            return {"error": "No SMILES string provided."}
        try:
            mol = Molecule(smiles)
            return dict(mol.functional_groups_all)
        except Exception as exc:
            return {"error": f"Failed to identify functional groups: {exc}"}

def similarity_search_3d(smiles: str, threshold: int = 80, max_records: int = 50) -> dict:
    """
    Perform 3D similarity search using SMILES and return similar compounds with their names and SMILES.
    
    Args:
        smiles: SMILES string of the query compound
        threshold: Similarity threshold (0-100, default 80)
        max_records: Maximum number of results to return (default 50, max 100)
    
    Returns:
        Dictionary containing similar compounds with their CIDs, names, and SMILES
    """
    from urllib.parse import quote
    
    headers = {"User-Agent": "just-chat-chemistry-tools/1.0"}
    
    try:
        # Step 1: Get CIDs from 3D similarity search
        encoded_smiles = quote(smiles)
        similarity_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/fastsimilarity_3d/smiles/{encoded_smiles}/cids/JSON?Threshold={threshold}&MaxRecords={max_records}"
        
        response = requests.get(similarity_url, timeout=300, headers=headers)  # 5 minute timeout
        response.raise_for_status()
        data = response.json()
        
        if "IdentifierList" not in data or "CID" not in data["IdentifierList"]:
            return {"error": "No similar compounds found"}
        
        cids = data["IdentifierList"]["CID"]
        
        if not cids:
            return {"error": "No similar compounds found"}
        
        # Step 2: Get properties for all CIDs
        cids_str = ",".join(map(str, cids))
        properties_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cids_str}/property/SMILES,IUPACName,Title/JSON"
        
        prop_response = requests.get(properties_url, timeout=300, headers=headers)  # 5 minute timeout
        prop_response.raise_for_status()
        prop_data = prop_response.json()
        
        if "PropertyTable" not in prop_data or "Properties" not in prop_data["PropertyTable"]:
            return {"error": "Failed to retrieve compound properties"}
        
        properties = prop_data["PropertyTable"]["Properties"]
        
        # Step 3: Organize results
        results = []
        for prop in properties:
            cid = prop.get("CID")
            smiles_result = prop.get("SMILES")
            iupac_name = prop.get("IUPACName")
            title = prop.get("Title")
            
            # Prefer Title (common name), fallback to IUPACName
            name = title or iupac_name or "Unknown"
            
            results.append({
                "cid": cid,
                "name": name,
                "smiles": smiles_result
            })
        
        return {
            "query_smiles": smiles,
            "threshold": threshold,
            "total_results": len(results),
            "results": results
        }
        
    except Exception as e:
        return {"error": f"Failed to perform 3D similarity search: {e}"}

def smarts_to_name(smarts: str, max_records: int = 1) -> dict:
    """
    Given a SMARTS pattern, perform a PubChem substructure search and return the best match's name and properties.

    Returns dict with keys:
      - query_smarts
      - match_count
      - cid (first hit) or None
      - name (Title or IUPACName) or None
      - smiles (CanonicalSMILES) or None
      - formula
      - molecular_weight
      - match_type ("first" | "none")
      - error (present on failure)
    """
    from urllib.parse import quote
    headers = {"User-Agent": "just-chat-chemistry-tools/1.0"}

    if not smarts or not smarts.strip():
        return {"error": "No SMARTS pattern provided."}

    try:
        encoded = quote(smarts.strip(), safe='')
        search_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/fastsubstructure/smarts/{encoded}/cids/JSON?MaxRecords={int(max_records)}"
        resp = requests.get(search_url, timeout=120, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        cids = data.get("IdentifierList", {}).get("CID", [])
        match_count = len(cids)
        if not cids:
            return {"query_smarts": smarts, "match_count": 0, "match_type": "none", "cid": None}
        cid = cids[0]
        props_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/Title,IUPACName,CanonicalSMILES,MolecularFormula,MolecularWeight/JSON"
        presp = requests.get(props_url, timeout=30, headers=headers)
        presp.raise_for_status()
        pdata = presp.json()
        prop = pdata.get("PropertyTable", {}).get("Properties", [{}])[0]
        name = prop.get("Title") or prop.get("IUPACName")
        return {
            "query_smarts": smarts,
            "match_count": match_count,
            "cid": cid,
            "name": name,
            "smiles": prop.get("CanonicalSMILES"),
            "formula": prop.get("MolecularFormula"),
            "molecular_weight": prop.get("MolecularWeight"),
            "match_type": "first"
        }
    except Exception as exc:
        return {"error": f"SMARTS lookup failed: {exc}"}

def smarts_to_molecular_weight(smarts: str, max_records: int = 1) -> dict:
    """
    Return the molecular weight for the best PubChem hit matching the SMARTS pattern.

    Uses PubChem properties (MolecularWeight) for the first matching CID.
    """
    # Reuse smarts_to_name which fetches MolecularWeight as part of properties
    res = smarts_to_name(smarts, max_records=max_records)
    if res.get("error"):
        return res
    if res.get("molecular_weight") is None:
        return {"error": "Molecular weight not available for matched compound.", "cid": res.get("cid")}
    try:
        return {"query_smarts": smarts, "cid": res.get("cid"), "molecular_weight": float(res.get("molecular_weight"))}
    except Exception:
        return {"query_smarts": smarts, "cid": res.get("cid"), "molecular_weight": res.get("molecular_weight")}

def name_to_smarts(name: str) -> dict:
    """
    Resolve a chemical name to SMARTS.

    Strategy:
      - Use existing name_to_smiles() to get a SMILES string via PubChem
      - Use lazy RDKit import via _get_rdkit_chem() to convert SMILES -> SMARTS
      - If RDKit is not installed, return the SMILES and an actionable error message

    Returns a dict:
      - name
      - smiles
      - smarts (or None)
      - error (present on failure)
    """
    if not name or not name.strip():
        return {"error": "No compound name provided."}
    try:
        smiles = name_to_smiles(name)
        if not smiles or (isinstance(smiles, str) and (smiles.startswith("Error") or smiles.startswith("SMILES not found") or smiles.startswith("PubChem lookup failed"))):
            return {"error": f"Could not resolve name to SMILES: {smiles}"}
    except Exception as exc:
        return {"error": f"Failed to resolve name to SMILES: {exc}"}

    # Convert SMILES -> SMARTS using lazy RDKit import
    try:
        Chem = _get_rdkit_chem()
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return {"name": name, "smiles": smiles, "error": "RDKit failed to parse SMILES."}
        smarts = Chem.MolToSmarts(mol)
        return {"name": name, "smiles": smiles, "smarts": smarts}
    except ImportError as ie:
        return {
            "name": name,
            "smiles": smiles,
            "error": str(ie)
        }
    except Exception as rd_exc:
        return {"name": name, "smiles": smiles, "error": f"RDKit conversion failed: {rd_exc}"}

    
if __name__ == "__main__":
    # Example: Aspirin SMILES
    smiles = "CC(=O)OC1=CC=CC=C1C(=O)O"
    result = identify_functional_groups(smiles=smiles)
    print("Functional groups for Aspirin:", result)
