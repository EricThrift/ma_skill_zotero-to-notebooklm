"""
sync_collection.py

This script synchronizes references from a specified Zotero collection to a Google NotebookLM notebook.
It fetches items and their PDF attachments via the Zotero Web API, downloads them, uploads them
to Google NotebookLM using the CLI, automatically labels them under the collection name,
and updates the local tracking file (zotero_uploaded_items.json).

Core Functions:
- label_source: Groups/labels uploaded PDF files inside Google NotebookLM.
- resolve_notebook_id: Fetches the unique Notebook ID based on the notebook name.
- attempt_resize_pdf: Downsizes PDFs exceeding 25MB via native stream compression or Ghostscript.
- main: The main synchronization loop checking Web API items and calling upload routines.

When to call:
- Run this script when doing an initial import of a Zotero collection into a NotebookLM notebook.
- Run this script on a regular basis (or after adding papers to Zotero) to keep Google NotebookLM up-to-date with new additions.
"""

import subprocess
import json
import os
import re
import sys
import argparse
import urllib.request
import urllib.parse
import ssl
from pyzotero import zotero

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Load credentials from .env
env_path = ".env"
env_vars = {}
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

library_id = env_vars.get("ZOTERO_LIBRARY_ID")
library_type = env_vars.get("ZOTERO_LIBRARY_TYPE")
api_key = env_vars.get("ZOTERO_API_KEY")

if not library_id or not api_key:
    print("Error: Missing Zotero credentials in .env.")
    sys.exit(1)

zot = zotero.Zotero(library_id, library_type, api_key)

def label_source(notebook_id, source_title, collection_name):
    """
    Finds or creates a label in NotebookLM matching the Zotero collection name
    and assigns the uploaded source to that label.

    Inputs:
        notebook_id (str): The unique ID of the target NotebookLM notebook.
        source_title (str): The title of the source document in NotebookLM.
        collection_name (str): The Zotero collection name used as the label name.

    Returns:
        bool: True if labeling was successful, False otherwise.
    """
    # 1. Get or create label ID
    print(f"Finding or creating label for collection '{collection_name}'...")
    label_id = None
    
    cmd_list_labels = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "label", "list", notebook_id, "--json"]
    res_list = subprocess.run(cmd_list_labels, capture_output=True, text=True, encoding="utf-8")
    if res_list.returncode == 0:
        try:
            data = json.loads(res_list.stdout)
            labels = data if isinstance(data, list) else data.get("labels", [])
            for lbl in labels:
                if lbl.get("name") == collection_name:
                    label_id = lbl.get("id")
                    break
        except Exception:
            pass
            
    if not label_id:
        print(f"Label '{collection_name}' not found. Creating it...")
        cmd_create_label = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "label", "create", notebook_id, collection_name]
        subprocess.run(cmd_create_label, capture_output=True, text=True, encoding="utf-8")
        
        res_list = subprocess.run(cmd_list_labels, capture_output=True, text=True, encoding="utf-8")
        if res_list.returncode == 0:
            try:
                data = json.loads(res_list.stdout)
                labels = data if isinstance(data, list) else data.get("labels", [])
                for lbl in labels:
                    if lbl.get("name") == collection_name:
                        label_id = lbl.get("id")
                        break
            except Exception:
                pass
                
    if not label_id:
        print(f"Warning: Could not resolve label ID for '{collection_name}'. Skipping labeling.")
        return False
        
    # 2. Get source ID for the uploaded title
    print(f"Resolving source ID for '{source_title}'...")
    source_id = None
    cmd_list_sources = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "list", "sources", notebook_id, "--json"]
    res_sources = subprocess.run(cmd_list_sources, capture_output=True, text=True, encoding="utf-8")
    if res_sources.returncode == 0:
        try:
            sources = json.loads(res_sources.stdout)
            for src in sources:
                if src.get("title") == source_title:
                    source_id = src.get("id")
                    break
        except Exception:
            pass
            
    if not source_id:
        print(f"Warning: Could not find source '{source_title}' in NotebookLM. Skipping labeling.")
        return False
        
    # 3. Move/assign source to label
    print(f"Assigning source '{source_title}' to label '{collection_name}'...")
    cmd_move = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "label", "move", notebook_id, source_id, label_id]
    res_move = subprocess.run(cmd_move, capture_output=True, text=True, encoding="utf-8")
    if res_move.returncode == 0:
        print(f"Successfully labeled '{source_title}' as '{collection_name}'")
        return True
    else:
        print(f"Warning: Failed to move source to label: {res_move.stderr}")
        return False

def resolve_notebook_id(notebook_name):
    """
    Retrieves the unique Google NotebookLM notebook ID for a given notebook title.

    Inputs:
        notebook_name (str): The name/title of the target NotebookLM notebook.

    Returns:
        str or None: The notebook ID string if found, otherwise None.
    """
    # Find notebook ID by title
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

def attempt_resize_pdf(input_path, output_path):
    """
    Attempts to compress/resize a PDF file to fit within Google's 25MB file size limit.
    Tries native pypdf stream compression first, falling back to Ghostscript CLI if available.

    Inputs:
        input_path (str): The path to the source PDF file.
        output_path (str): The path where the compressed PDF should be written.

    Returns:
        bool: True if compression was successful and the output file is < 25MB, False otherwise.
    """
    try:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for page in reader.pages:
            page.compress_content_streams()
            writer.add_page(page)
        with open(output_path, "wb") as f:
            writer.write(f)
        if os.path.exists(output_path) and os.path.getsize(output_path) < 25 * 1024 * 1024:
            print("Successfully compressed PDF using pypdf.")
            return True
    except Exception as e:
        print(f"pypdf compression attempt failed/skipped: {e}")
        
    try:
        cmd = [
            "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/screen", "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={output_path}", input_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(output_path):
            if os.path.getsize(output_path) < 25 * 1024 * 1024:
                print("Successfully compressed PDF using Ghostscript.")
                return True
    except Exception:
        pass
    return False

def main():
    """
    Main execution logic to connect to Zotero via Web API, fetch items,
    retrieve PDF attachments, upload them to NotebookLM, and update mapping database.

    Inputs:
        None (uses argparse CLI flags)

    Returns:
        None
    """
    parser = argparse.ArgumentParser(description="Synchronize collections from Zotero to NotebookLM using the Web API")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Sync all previously uploaded collections")
    group.add_argument("--collection-name", type=str, help="Sync a specific collection by its Zotero name")
    group.add_argument("--list", action="store_true", help="List currently synchronized collections")
    
    args = parser.parse_args()
    output_file = "zotero_uploaded_items.json"
    
    # 1. Handle Listing
    if args.list:
        if not os.path.exists(output_file):
            print("No synchronization records found in zotero_uploaded_items.json.")
            return
        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        if "source_library" in data:
            lib = data["source_library"]
            print(f"Target NotebookLM Notebook: {lib.get('name')}")
            print("\nSynchronized Zotero Collections:")
            print(f"  - Collection: {lib.get('name')} (key: {lib.get('collection_key')}) - {len(data.get('uploaded_items', []))} items")
        else:
            notebook = data.get("target_notebook", {})
            print(f"Target NotebookLM Notebook: {notebook.get('name')} (ID: {notebook.get('notebook_id')})")
            print("\nSynchronized Zotero Collections:")
            for lib in data.get("source_libraries", []):
                print(f"Group Library: {lib.get('name')} (groupID: {lib.get('groupID')})")
                for col in lib.get("collections", []):
                    print(f"  - Collection: {col.get('name')} (key: {col.get('collection_key')}) - {len(col.get('uploaded_items', []))} items")
        return

    # Check NotebookLM CLI auth
    print("Checking NotebookLM connection...")
    res_info = subprocess.run(["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "doctor"], capture_output=True, text=True, encoding="utf-8")
    if res_info.returncode != 0:
        print("Error: NotebookLM CLI is not authenticated. Run 'nlm login' first.")
        print(res_info.stderr)
        sys.exit(1)

    # Load existing mapping supporting both schemas
    mapping_data = {
        "source_library": {
            "groupID": int(library_id) if library_type == "group" else 0,
            "collection_key": "PLACEHOLDER",
            "name": args.collection_name or "Restorative cataloguing"
        },
        "uploaded_items": []
    }
    
    if os.path.exists(output_file):
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if "source_library" in loaded:
                    mapping_data["source_library"] = loaded["source_library"]
                if "uploaded_items" in loaded:
                    mapping_data["uploaded_items"] = loaded["uploaded_items"]
                elif "source_libraries" in loaded:
                    # Fallback for older nested schema
                    uploaded = []
                    for lib in loaded.get("source_libraries", []):
                        for col in lib.get("collections", []):
                            uploaded.extend(col.get("uploaded_items", []))
                    mapping_data["uploaded_items"] = uploaded
        except Exception as e:
            print(f"Warning: Could not read {output_file}: {e}")

    notebook_name = args.collection_name or mapping_data["source_library"].get("name", "Restorative cataloguing")
    print(f"Resolving Notebook ID for '{notebook_name}'...")
    notebook_id = resolve_notebook_id(notebook_name)
    if not notebook_id:
        print(f"Notebook '{notebook_name}' not found. Attempting to create it in NotebookLM...")
        cmd_create = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "notebook", "create", notebook_name, "--json"]
        res_create = subprocess.run(cmd_create, capture_output=True, text=True, encoding="utf-8")
        if res_create.returncode == 0:
            try:
                created_data = json.loads(res_create.stdout)
                notebook_id = created_data.get("notebook_id")
                print(f"Successfully created notebook '{notebook_name}' with ID: {notebook_id}")
            except Exception as e:
                print(f"Error parsing notebook creation output: {e}")
        else:
            print(f"Error creating notebook '{notebook_name}': {res_create.stderr}")
            
    if not notebook_id:
        print(f"Error: Could not find, create or resolve NotebookLM notebook named '{notebook_name}'.")
        sys.exit(1)

    print(f"Target Notebook ID: {notebook_id}")

    # Fetch all collections via Web API to find the key
    print("Fetching Zotero collections list...")
    collections = []
    start = 0
    limit = 100
    while True:
        chunk = zot.collections(limit=limit, start=start)
        if not chunk:
            break
        collections.extend(chunk)
        if len(chunk) < limit:
            break
        start += limit

    collections_to_sync = []
    
    if args.collection_name:
        matched_col = None
        for col in collections:
            if col["data"]["name"].lower() == args.collection_name.lower():
                matched_col = col
                break
        if not matched_col:
            print(f"Error: Collection '{args.collection_name}' not found in Zotero library.")
            sys.exit(1)
            
        collections_to_sync.append({
            "collection_key": matched_col["key"],
            "collection_name": matched_col["data"]["name"],
            "groupID": int(library_id) if library_type == "group" else 0,
            "group_name": "Group Library" if library_type == "group" else "My Library"
        })
    else:  # --all
        col_key = mapping_data["source_library"].get("collection_key")
        if not col_key or col_key == "PLACEHOLDER":
            print("No collections registered in zotero_uploaded_items.json to sync.")
            return
            
        matched_col = None
        for col in collections:
            if col["key"] == col_key:
                matched_col = col
                break
        if matched_col:
            collections_to_sync.append({
                "collection_key": col_key,
                "collection_name": matched_col["data"]["name"],
                "groupID": int(library_id) if library_type == "group" else 0,
                "group_name": "Group Library" if library_type == "group" else "My Library"
            })

    all_failed_items = []

    # 4. Sync each collection
    for col_info in collections_to_sync:
        col_key = col_info["collection_key"]
        col_name = col_info["collection_name"]
        group_id = col_info["groupID"]
        
        print(f"\nSyncing collection '{col_name}' ({col_key})...")
        
        # Get existing keys in mapping
        existing_keys = {item["zotero_key"] for item in mapping_data.get("uploaded_items", [])}

        # Query all items in this collection via Web API
        print("Fetching collection items...")
        all_items = []
        start = 0
        limit = 100
        while True:
            chunk = zot.collection_items(col_key, limit=limit, start=start)
            if not chunk:
                break
            all_items.extend(chunk)
            if len(chunk) < limit:
                break
            start += limit

        parent_items = [item for item in all_items if item["data"].get("itemType") not in ["attachment", "note"]]
        
        to_upload = []
        for item in parent_items:
            parent_key = item["key"]
            if parent_key in existing_keys:
                continue
                
            title = item["data"].get("title", "")
            date = item["data"].get("date", "")
            
            # Author
            creators = item["data"].get("creators", [])
            author = "Unknown"
            if creators:
                first_creator = creators[0]
                author = first_creator.get("lastName") or first_creator.get("name") or "Unknown"
                
            # Year
            year = "Unknown"
            if date:
                m = re.search(r'\b(19|20)\d{2}\b', date)
                if m:
                    year = m.group(0)
                else:
                    year = date[:4]
                    
            type_name = item["data"].get("itemType", "document")
            
            # Query child attachments via Web API
            children = zot.children(parent_key)
            attachments = []
            for child in children:
                c_data = child["data"]
                if c_data.get("itemType") == "attachment" and c_data.get("contentType") == "application/pdf":
                    attachments.append(child)
                    
            for att in attachments:
                att_key = att["key"]
                filename = att["data"].get("filename") or f"{parent_key}_attachment.pdf"
                to_upload.append({
                    "attachment_key": att_key,
                    "filename": filename,
                    "parent_key": parent_key,
                    "author": author,
                    "year": year,
                    "date": date,
                    "type_name": type_name,
                    "title": title
                })

        if not to_upload:
            print(f"Collection '{col_name}' is already up-to-date.")
            continue
            
        print(f"Uploading {len(to_upload)} new PDF attachments to NotebookLM...")
        uploaded_count = 0
        uploaded_metadata = []
        
        for task in to_upload:
            att_key = task["attachment_key"]
            filename = task["filename"]
            parent_key = task["parent_key"]
            author = task["author"]
            year = task["year"]
            date = task["date"]
            type_name = task["type_name"]
            title = task["title"]
            
            target_title = f"{author}-{year}-{parent_key}"
            print(f"[{uploaded_count+len(all_failed_items)+1}/{len(to_upload)}] Downloading attachment '{filename}' from Zotero...")
            
            local_path = filename
            try:
                # Download using pyzotero's dump method
                zot.dump(att_key, local_path)
                
                file_size = os.path.getsize(local_path)
                upload_path = local_path
                
                # Check size constraints
                if file_size > 25 * 1024 * 1024:
                    print(f"Downloaded PDF is too large: {file_size / (1024*1024):.1f}MB (limit: 25MB). Resizing...")
                    resized_path = local_path + ".resized.pdf"
                    if attempt_resize_pdf(local_path, resized_path):
                        upload_path = resized_path
                    else:
                        print("Warning: Could not resize PDF to fit 25MB constraint.")
                        upload_path = None
                        
                if not upload_path:
                    print(f"Error: PDF is too large. Skipping {target_title}.")
                    all_failed_items.append(f"{target_title} (PDF exceeds size limit)")
                    if os.path.exists(local_path):
                        os.remove(local_path)
                    continue
                    
                print(f"Uploading '{target_title}' to NotebookLM...")
                cmd_upload = [
                    "uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "source", "add",
                    notebook_id,
                    "--file", upload_path,
                    "--title", target_title,
                    "--wait"
                ]
                res_upload = subprocess.run(cmd_upload, capture_output=True, text=True, encoding="utf-8")
                
                # Clean up local files
                if os.path.exists(local_path):
                    os.remove(local_path)
                resized_temp = local_path + ".resized.pdf"
                if os.path.exists(resized_temp):
                    os.remove(resized_temp)
                    
                if res_upload.returncode == 0:
                    print(f"Successfully uploaded: {target_title}")
                    label_source(notebook_id, target_title, col_name)
                    
                    uploaded_count += 1
                    uploaded_metadata.append({
                        "author": author,
                        "date": date,
                        "zotero_key": parent_key,
                        "title": title,
                        "itemtype": type_name
                    })
                else:
                    print(f"Failed to upload {target_title}:")
                    print(res_upload.stderr)
                    all_failed_items.append(f"{target_title} (NotebookLM upload command failed)")
                    
            except Exception as e:
                print(f"Error processing attachment {filename}: {e}")
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                    except:
                        pass
                all_failed_items.append(f"{target_title} (Download/process error)")

        # Merge results into mapping_data
        if uploaded_metadata:
            mapping_data["source_library"] = {
                "groupID": group_id,
                "collection_key": col_key,
                "name": col_name
            }
            
            existing_items_dict = {item["zotero_key"]: item for item in mapping_data["uploaded_items"]}
            for item in uploaded_metadata:
                existing_items_dict[item["zotero_key"]] = item
            mapping_data["uploaded_items"] = list(existing_items_dict.values())
            
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(mapping_data, f, indent=2, ensure_ascii=False)
            print(f"Recorded metadata for {len(uploaded_metadata)} new items in {output_file}")

    print("\nSynchronization complete.")
    
    if all_failed_items:
        print("\n" + "="*80)
        print("ALERT: The following sources in the Zotero collection were ignored or not successfully uploaded:")
        for item in all_failed_items:
            print(f" - {item}")
        print("="*80 + "\n")

if __name__ == "__main__":
    main()
