import requests
import eliot
import os
import sys

def get_ghs_classification(compound_input: str, input_type: str = "auto") -> dict:
    """
    Retrieve GHS classification from PubChem PUG-View, including hazard classes, categories,
    signal word, hazard statements (H-codes), and pictograms.

    Args:
        compound_input: name, SMILES, or CID
        input_type: "auto" | "name" | "smiles" | "cid"

    Returns:
        dict with keys: cid, signal_word, pictograms, hazard_classes, hazard_statements
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

    # Fetch PUG-View data
    try:
        view_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON/"
        resp = requests.get(view_url, timeout=30, headers=headers)
        resp.raise_for_status()
        view = resp.json()
    except Exception as e:
        return {"error": f"Failed to fetch PUG-View data: {e}"}

    # Traverse sections helper
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

    # Collect GHS-related data
    signal_word = None
    pictograms = []
    hazard_classes = []  # items like {class, category}
    hazard_statements = []  # items like {code, text}

    record = (view or {}).get("Record", {})
    for sec in iterate_sections(record.get("Section", [])):
        heading = (sec.get("TOCHeading") or "").lower()
        if not any(h in heading for h in ["ghs", "globally harmonized", "hazard classification", "hazards", "safety"]):
            continue

        # Information entries can contain StringWithMarkup with structured content
        for info in (sec.get("Information") or []):
            val = info.get("Value") or {}
            strings = [s.get("String") for s in (val.get("StringWithMarkup") or []) if s.get("String")]
            # Extract possible external image/data URLs that may include pictograms
            urls = []
            if isinstance(val.get("ExternalDataURL"), list):
                urls.extend([u for u in val.get("ExternalDataURL") if isinstance(u, str)])
            if isinstance(val.get("URL"), list):
                urls.extend([u for u in val.get("URL") if isinstance(u, str)])
            if isinstance(val.get("ExternalDataURL"), str):
                urls.append(val.get("ExternalDataURL"))
            if isinstance(val.get("URL"), str):
                urls.append(val.get("URL"))

            # Try to detect GHS pictogram codes in strings or URLs and capture images
            detected_codes = []
            for s in strings:
                if not s:
                    continue
                for m in re.finditer(r"\bGHS0?([1-9])\b", s, flags=re.IGNORECASE):
                    detected_codes.append(f"GHS0{m.group(1)}")
                if any(k in s.lower() for k in ["skull", "flame", "exclamation", "corrosion", "gas cylinder", "health hazard", "environment", "exploding bomb"]):
                    # Keep free-text hint; URL matching below may attach an image
                    detected_codes.append(s)
            for u in urls:
                if not isinstance(u, str):
                    continue
                m = re.search(r"(GHS0?[1-9])", u, flags=re.IGNORECASE)
                if m:
                    detected_codes.append(m.group(1).upper())

            # If this Information block looks like pictograms, add structured entries
            info_name = (info.get("Name") or "").lower()
            looks_like_picto = ("pictogram" in info_name) or any("pictogram" in (s or '').lower() for s in strings)
            if looks_like_picto or detected_codes or urls:
                # Map codes to matching URLs when possible
                used_pairs = set()
                for code in {c for c in detected_codes if isinstance(c, str) and c.upper().startswith("GHS")}:
                    matched_url = None
                    for u in urls:
                        if isinstance(u, str) and code.lower() in u.lower():
                            matched_url = u
                            break
                    key = (code.upper(), matched_url)
                    if key in used_pairs:
                        continue
                    used_pairs.add(key)
                    pictograms.append({"code": code.upper(), "image_url": matched_url})
                # Add any remaining URLs without detected code as generic pictograms
                for u in urls:
                    key = (None, u)
                    if key in used_pairs:
                        continue
                    used_pairs.add(key)
                    if isinstance(u, str):
                        pictograms.append({"code": None, "image_url": u})

            for s in strings:
                s_l = s.lower()
                # Signal word
                if not signal_word and ("signal word:" in s_l or s_l.startswith("signal word")):
                    # e.g., "Signal word: Danger"
                    parts = s.split(":", 1)
                    if len(parts) == 2:
                        signal_word = parts[1].strip()
                    else:
                        # fallback: last token
                        signal_word = s.strip().split()[-1]

                # Hazard classes and categories
                # Example: "Acute toxicity (oral) - Category 3"
                m = re.search(r"([A-Za-z ].*?\))\s*-\s*Category\s*(\d+[A-Za-z]?)", s)
                if m:
                    hazard_classes.append({
                        "class": m.group(1).strip(),
                        "category": m.group(2).strip()
                    })

                # Hazard statements H-codes
                # Example: "H225: Highly flammable liquid and vapor"
                hm = re.search(r"\b(H\d{3}[A-Z]?)\b\s*:\s*(.+)$", s)
                if hm:
                    hazard_statements.append({
                        "code": hm.group(1),
                        "text": hm.group(2).strip()
                    })

            # Also inspect Name/Description fields for structured tags
            name = info.get("Name") or ""
            if name.lower().startswith("signal word") and not signal_word:
                # Try extract from Description/String if present
                desc = info.get("Description") or ""
                if desc:
                    signal_word = desc.strip()

    # Normalize pictograms: ensure list of dicts with code and image_url, dedupe by (code,url)
    normalized = []
    seen = set()
    for p in pictograms:
        if isinstance(p, dict):
            code = p.get("code")
            url = p.get("image_url")
        else:
            code = None
            url = None
        key = (code, url)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"code": code, "image_url": url})
    pictograms = normalized

    # Fallback mapping for standard GHS diamond pictogram images (Wikimedia Commons)
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

    # Ensure each pictogram with a known code has an image_url
    for p in pictograms:
        code = p.get("code")
        if code and not p.get("image_url"):
            fallback_url = code_to_fallback_image.get(code.upper())
            if fallback_url:
                p["image_url"] = fallback_url

    # Prepare markdown image snippets for easy rendering in UI
    pictogram_markdown = []
    for p in pictograms:
        code = p.get("code") or "GHS"
        url = p.get("image_url")
        if url:
            pictogram_markdown.append(f"![{code}]({url})")

    result = {
        "cid": cid,
        "signal_word": signal_word,
        "pictograms": pictograms,
        "pictogram_markdown": pictogram_markdown,
        "hazard_classes": hazard_classes,
        "hazard_statements": hazard_statements
    }

    # Provide a helpful message if nothing found
    if not any([signal_word, pictograms, hazard_classes, hazard_statements]):
        result["note"] = "No explicit GHS data found in PubChem PUG-View sections."
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
    try:
        encoded_smiles = quote(smiles.strip())
        url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{encoded_smiles}/"
            "property/MolecularWeight/JSON"
        )
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status()
        data = response.json()
        props = data.get("PropertyTable", {}).get("Properties", [])
        if not props:
            return {"error": "No property data found in PubChem response"}
        weight = props[0].get("MolecularWeight")
        if weight is None:
            return {"error": "MolecularWeight not found in PubChem response"}
        return float(weight)
    except Exception as exc:
        return {"error": f"Failed to retrieve molecular weight: {exc}"}

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

if __name__ == "__main__":
    # Example: Aspirin SMILES
    smiles = "CC(=O)OC1=CC=CC=C1C(=O)O"
    result = identify_functional_groups(smiles=smiles)
    print("Functional groups for Aspirin:", result)
