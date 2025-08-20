import requests
import json

def test_3d_similarity_search():
    """Test 3D similarity search with PubChem API"""
    
    # Test 3D similarity search
    smiles = "CC(=O)OC1=CC=CC=C1C(=O)O"  # Aspirin
    
    # Get CIDs from 3D similarity search
    similarity_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/fastsimilarity_3d/smiles/{smiles}/cids/JSON?Threshold=80&MaxRecords=10"
    
    try:
        response = requests.get(similarity_url, timeout=15)
        print(f"Similarity search status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if "IdentifierList" in data and "CID" in data["IdentifierList"]:
                cids = data["IdentifierList"]["CID"]
                print(f"Found {len(cids)} similar compounds: {cids}")
                
                # Get properties for first few CIDs
                if cids:
                    # Take first 3 CIDs for testing
                    test_cids = cids[:3]
                    cids_str = ",".join(map(str, test_cids))
                    
                    properties_url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cids_str}/property/SMILES,IUPACName,Title/JSON"
                    
                    prop_response = requests.get(properties_url, timeout=15)
                    print(f"Properties request status: {prop_response.status_code}")
                    
                    if prop_response.status_code == 200:
                        prop_data = prop_response.json()
                        print("Properties response:")
                        print(json.dumps(prop_data, indent=2))
                    else:
                        print(f"Properties request failed: {prop_response.text}")
            else:
                print("No CIDs found in similarity search")
        else:
            print(f"Similarity search failed: {response.text}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_3d_similarity_search()
