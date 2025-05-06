import requests

def smiles_to_name(smiles: str) -> str:
    """
    Given a SMILES string, query PubChem and return the compound's name.
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
    Given a molecule name, query PubChem and return the canonical SMILES string.
    """
    url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/property/CanonicalSMILES/JSON"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        props = data["PropertyTable"]["Properties"][0]
        return props.get("CanonicalSMILES") or "SMILES not found in PubChem."
    except Exception as e:
        return f"Error in PubChem lookup: {e}"