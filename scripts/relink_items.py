"""
relink_items.py

This script reconstructs the metadata mapping file (zotero_uploaded_items.json) between 
sources in Google NotebookLM and references in a Zotero library. It parses Zotero item keys 
from NotebookLM source titles, queries their metadata from the Zotero Web API, and matches 
them back to the original library collection.

Core Functions:
- load_credentials: Loads Zotero credentials from environment or .env.
- resolve_notebook_id: Resolves NotebookLM notebook name to a unique ID.
- main: Connects to Zotero, processes NotebookLM sources, and outputs the mapping registry.

When to call:
- Run this script when the local mapping file `zotero_uploaded_items.json` is missing or out of sync.
- It is called automatically as a downstream step in other integration workflows (e.g. update_citation_keys.py).
"""

import subprocess
import json
import os
import re
import sys
import argparse
from pyzotero import zotero

def load_credentials():
    """
    Loads Zotero credentials from the local .env file or environment variables.

    Inputs:
        None

    Returns:
        tuple: A tuple containing (library_id, library_type, api_key) as strings.
    """
    env_path = ".env"
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()
                    
    library_id = os.environ.get("ZOTERO_LIBRARY_ID") or env_vars.get("ZOTERO_LIBRARY_ID")
    library_type = os.environ.get("ZOTERO_LIBRARY_TYPE") or env_vars.get("ZOTERO_LIBRARY_TYPE")
    api_key = os.environ.get("ZOTERO_API_KEY") or env_vars.get("ZOTERO_API_KEY")
    
    if not library_id or not library_type or not api_key:
        print("Error: Zotero credentials are required in .env file.")
        sys.exit(1)
        
    return library_id, library_type, api_key

def resolve_notebook_id(notebook_name):
    """
    Retrieves the unique Google NotebookLM notebook ID for a given notebook title.

    Inputs:
        notebook_name (str): The name/title of the target NotebookLM notebook.

    Returns:
        str or None: The notebook ID string if found, otherwise None.
    """
    cmd_list = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "list", "notebooks"]
    res_list = subprocess.run(cmd_list, capture_output=True, text=True, encoding="utf-8")
    if res_list.returncode == 0:
        try:
            notebooks = json.loads(res_list.stdout)
            for nb in notebooks:
                if nb.get("title") == notebook_name:
                    return nb.get("id")
        except Exception:
            pass
    return None

def main():
    """
    Main execution logic to extract Zotero keys from NotebookLM source titles, 
    fetch their Zotero metadata, and rebuild the local zotero_uploaded_items.json registry.

    Inputs:
        None (uses argparse CLI arguments --notebook-name)

    Returns:
        None
    """
    parser = argparse.ArgumentParser(description="Relink items between Zotero and NotebookLM to rebuild metadata mapping JSON.")
    parser.add_argument("--notebook-name", type=str, required=True, help="Target NotebookLM Notebook Name")
    
    args = parser.parse_args()
    
    # Load credentials
    library_id, library_type, api_key = load_credentials()
    zot = zotero.Zotero(library_id, library_type, api_key)
    
    # Check NotebookLM connection
    print("Checking NotebookLM connection...")
    res_info = subprocess.run(["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "doctor"], capture_output=True, text=True, encoding="utf-8")
    if res_info.returncode != 0:
        print("Error: NotebookLM CLI is not authenticated. Run 'nlm login' first.")
        print(res_info.stderr)
        sys.exit(1)
        
    notebook_id = resolve_notebook_id(args.notebook_name)
    if not notebook_id:
        print(f"Error: Notebook '{args.notebook_name}' not found.")
        sys.exit(1)
        
    print(f"Found notebook '{args.notebook_name}' with ID: {notebook_id}")
    
    # List sources in notebook
    print("Listing sources in notebook...")
    cmd_sources = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "list", "sources", notebook_id, "--json"]
    res_sources = subprocess.run(cmd_sources, capture_output=True, text=True, encoding="utf-8")
    if res_sources.returncode != 0:
        print("Error: Could not retrieve sources from notebook.")
        print(res_sources.stderr)
        sys.exit(1)
        
    try:
        sources_list = json.loads(res_sources.stdout)
    except Exception as e:
        print(f"Error parsing sources list: {e}")
        sys.exit(1)
        
    print(f"Found {len(sources_list)} sources in the notebook.")
    
    # Extract keys from titles (format: {author}-{year}-{zotero_key})
    zotero_keys = []
    for src in sources_list:
        title = src.get("title", "")
        m = re.match(r"^.*-([A-Z0-9]{8})$", title)
        if m:
            zotero_keys.append(m.group(1))
            
    if not zotero_keys:
        print("No sources matching Zotero title format ({author}-{year}-{zotero_key}) were found in the notebook.")
        sys.exit(1)
        
    print(f"Extracted {len(zotero_keys)} potential Zotero keys from source titles.")
    
    # Fetch items from Zotero API in chunks of 50 (API limits itemKey queries)
    print("Fetching item metadata from Zotero Web API...")
    relinked_items = []
    chunk_size = 50
    for i in range(0, len(zotero_keys), chunk_size):
        chunk = zotero_keys[i:i + chunk_size]
        keys_str = ",".join(chunk)
        try:
            items = zot.items(itemKey=keys_str)
            for item in items:
                key = item["key"]
                data = item["data"]
                
                # Exclude attachments and notes
                if data.get("itemType") in ["attachment", "note"]:
                    continue
                    
                # Extract first creator's lastName
                author = "Unknown"
                creators = data.get("creators", [])
                if creators:
                    first = creators[0]
                    author = first.get("name") or first.get("lastName") or "Unknown"
                    
                relinked_items.append({
                    "author": author,
                    "date": data.get("date", ""),
                    "zotero_key": key,
                    "title": data.get("title", ""),
                    "itemtype": data.get("itemType")
                })
        except Exception as e:
            print(f"Warning: Failed to fetch metadata for keys {chunk}: {e}")
            
    # Resolve collection_key and library details
    collection_key = "Unknown"
    print("Listing Zotero collections to match name...")
    try:
        collections = zot.collections()
        for col in collections:
            if col["data"]["name"].lower() == args.notebook_name.lower():
                collection_key = col["key"]
                break
    except Exception as e:
        print(f"Warning: Could not fetch Zotero collections: {e}")
        
    # Build final registry output
    output_file = "zotero_uploaded_items.json"
    output_data = {
        "source_library": {
            "groupID": int(library_id) if library_type == "group" else 0,
            "collection_key": collection_key,
            "name": args.notebook_name
        },
        "uploaded_items": relinked_items
    }
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
        
    print(f"Successfully relinked and wrote {len(relinked_items)} items to {output_file}")

if __name__ == "__main__":
    main()
