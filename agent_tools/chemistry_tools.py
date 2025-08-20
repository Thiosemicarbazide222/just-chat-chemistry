import requests
import eliot

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
    from chem.molecule import Molecule  # IFG import (assumes installed)
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

if __name__ == "__main__":
    # Example: Aspirin SMILES
    smiles = "CC(=O)OC1=CC=CC=C1C(=O)O"
    result = identify_functional_groups(smiles=smiles)
    print("Functional groups for Aspirin:", result)
