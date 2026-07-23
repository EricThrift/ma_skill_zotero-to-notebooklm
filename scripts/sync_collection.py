"""
sync_collection.py

This script synchronizes references from a specified Zotero collection to a Google NotebookLM notebook.
It fetches items (including subcollections) and their attachments via the Zotero Web API, downloads them,
converts HTML snapshots to PDF using Edge, uploads them to Google NotebookLM using the CLI,
automatically labels them under the subcollection/collection names, and updates the local tracking file.
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

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

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

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from update_citation_keys import resolve_author, org_acronyms, save_acronyms, generate_acronym
except Exception:
    resolve_author = None

def get_zotero_citation_key(zot_client, item):
    """
    Resolves or calculates the Zotero citationKey for a parent item.
    1. Checks item['data'].get('citationKey')
    2. Parses item['data'].get('extra') for 'Citation Key: ...'
    3. Derives author, year, key and generates author-year-key
    4. Updates Zotero if citationKey was missing.
    """
    data = item.get("data", {})
    key = item.get("key", "")
    cit_key = data.get("citationKey")
    if cit_key and cit_key.strip():
        return cit_key.strip()

    extra = data.get("extra", "")
    if extra:
        m = re.search(r'(?:citation\s*key|citation-key):\s*([^\s\n]+)', extra, re.I)
        if m:
            return m.group(1).strip()

    title = data.get("title", "")
    url = data.get("url", "")
    date = data.get("date", "")

    year = "ND"
    if date:
        m = re.search(r'\b(19|20)\d{2}\b', date)
        if m:
            year = m.group(0)
        else:
            year = date[:4]

    if resolve_author:
        try:
            author = resolve_author(item, title, url)
        except Exception:
            creators = data.get("creators", [])
            author = "Unknown"
            if creators:
                first_creator = creators[0]
                author = first_creator.get("lastName") or first_creator.get("name") or "Unknown"
    else:
        creators = data.get("creators", [])
        author = "Unknown"
        if creators:
            first_creator = creators[0]
            author = first_creator.get("lastName") or first_creator.get("name") or "Unknown"

    cit_key = f"{author}-{year}-{key}"

    try:
        item["data"]["citationKey"] = cit_key
        zot_client.update_items([item])
        print(f"Assigned and saved citationKey '{cit_key}' to Zotero item {key}")
    except Exception as e:
        print(f"Note: Could not write citationKey to Zotero for {key}: {e}")

    return cit_key

def label_source(notebook_id, source_title, collection_name):
    """
    Finds or creates a label in NotebookLM matching the Zotero collection/subcollection name
    and assigns the uploaded source to that label.
    """
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
        print(f"Error: Could not resolve or create label '{collection_name}'. Skipping labeling.")
        return False
        
    print(f"Resolving source ID for '{source_title}'...")
    source_id = None
    cmd_list_sources = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "list", "sources", notebook_id, "--json"]
    try:
        res_sources = subprocess.run(cmd_list_sources, capture_output=True, text=True, encoding="utf-8", timeout=60)
        if res_sources.returncode == 0:
            sources = json.loads(res_sources.stdout)
            for src in sources:
                if src.get("title") == source_title:
                    source_id = src.get("id")
                    break
    except Exception as e:
        print(f"Error parsing sources: {e}")
        
    if not source_id:
        print(f"Error: Source '{source_title}' not found in NotebookLM. Skipping labeling.")
        return False
        
    print(f"Assigning source '{source_title}' to label '{collection_name}'...")
    cmd_move = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "label", "move", notebook_id, source_id, label_id]
    res_move = subprocess.run(cmd_move, capture_output=True, text=True, encoding="utf-8")
    if res_move.returncode == 0:
        print(f"Successfully labeled '{source_title}' as '{collection_name}'")
        return True
    else:
        print(f"Failed to assign label: {res_move.stderr}")
        return False

def resolve_notebook_id(notebook_name):
    cmd_list = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "notebook", "list", "--json"]
    res = subprocess.run(cmd_list, capture_output=True, text=True, encoding="utf-8")
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            notebooks = data if isinstance(data, list) else data.get("notebooks", [])
            for nb in notebooks:
                if nb.get("name") == notebook_name:
                    return nb.get("id")
        except Exception:
            pass
    return None

def attempt_resize_pdf(input_path, output_path):
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

def convert_html_to_pdf(html_path, pdf_path):
    edge_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "msedge"
    ]
    edge_bin = None
    for p in edge_paths:
        if os.path.exists(p) or p == "msedge":
            edge_bin = p
            if p != "msedge":
                break
    if not edge_bin:
        print("Error: Microsoft Edge executable not found. Cannot convert HTML snapshot to PDF.")
        return False
        
    cmd = [
        edge_bin,
        "--headless",
        "--disable-gpu",
        f"--print-to-pdf={pdf_path}",
        f"file:///{html_path}"
    ]
    print(f"Converting HTML to PDF via Edge: {html_path} -> {pdf_path}")
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode == 0 and os.path.exists(pdf_path):
        return True
    else:
        print(f"Edge HTML to PDF conversion failed: {res.stderr}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Synchronize Zotero collections to NotebookLM")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Sync all previously uploaded collections")
    group.add_argument("--collection-name", type=str, help="Sync a specific collection by name")
    group.add_argument("--list", action="store_true", help="List currently synchronized collections")
    
    args = parser.parse_args()
    output_file = "zotero_uploaded_items.json"
    
    if args.list:
        if not os.path.exists(output_file):
            print("No synchronization records found in zotero_uploaded_items.json.")
            return
        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        notebook = data.get("target_notebook", {})
        print(f"Target Notebook: {notebook.get('name')}")
        print(f"Synchronized collections: {data.get('source_library', {}).get('name')}")
        return

    print("Checking NotebookLM connection...")
    res_info = subprocess.run(["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "doctor"], capture_output=True, text=True, encoding="utf-8")
    if res_info.returncode != 0:
        print("Error: NotebookLM CLI is not authenticated.")
        sys.exit(1)

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
                if "uploaded_items" in loaded:
                    mapping_data["uploaded_items"] = loaded["uploaded_items"]
        except Exception as e:
            print(f"Warning: Could not read {output_file}: {e}")

    notebook_name = args.collection_name or mapping_data["source_library"].get("name", "Restorative cataloguing")
    print(f"Resolving Notebook ID for '{notebook_name}'...")
    notebook_id = resolve_notebook_id(notebook_name)
    if not notebook_id:
        print(f"Notebook '{notebook_name}' not found. Creating it...")
        cmd_create = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "notebook", "create", notebook_name, "--json"]
        res_create = subprocess.run(cmd_create, capture_output=True, text=True, encoding="utf-8")
        if res_create.returncode == 0:
            try:
                created_data = json.loads(res_create.stdout)
                notebook_id = created_data.get("notebook_id")
                print(f"Created notebook ID: {notebook_id}")
            except Exception as e:
                print(f"Error parsing notebook creation output: {e}")
        else:
            print(f"Error creating notebook: {res_create.stderr}")
            
    if not notebook_id:
        print(f"Error: Could not resolve NotebookLM notebook '{notebook_name}'")
        sys.exit(1)
        
    print(f"Target Notebook ID: {notebook_id}")

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
            print(f"Error: Collection '{args.collection_name}' not found.")
            sys.exit(1)
        collections_to_sync.append({
            "collection_key": matched_col["key"],
            "collection_name": matched_col["data"]["name"],
            "groupID": int(library_id) if library_type == "group" else 0
        })
    else:
        col_key = mapping_data["source_library"].get("collection_key")
        if not col_key or col_key == "PLACEHOLDER":
            print("No collections registered to sync.")
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
                "groupID": int(library_id) if library_type == "group" else 0
            })

    all_failed_items = []

    for col_info in collections_to_sync:
        col_key = col_info["collection_key"]
        col_name = col_info["collection_name"]
        group_id = col_info["groupID"]
        
        print(f"\nSyncing collection '{col_name}' ({col_key})...")
        
        existing_keys = {item["zotero_key"] for item in mapping_data.get("uploaded_items", [])}

        print("Fetching existing NotebookLM sources for duplicate prevention...")
        cmd_list_sources = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "list", "sources", notebook_id, "--json"]
        nlm_titles = set()
        nlm_key_map = {}
        res_sources = subprocess.run(cmd_list_sources, capture_output=True, text=True, encoding="utf-8")
        if res_sources.returncode == 0:
            try:
                sources_list = json.loads(res_sources.stdout)
                for src in sources_list:
                    s_title = src.get("title", "")
                    if s_title:
                        nlm_titles.add(s_title)
                        m = re.search(r'([A-Z0-9]{8})$', s_title)
                        if m:
                            nlm_key_map[m.group(1)] = s_title
            except Exception as e:
                print(f"Warning: Could not parse NotebookLM sources list: {e}")

        # Resolve subcollections of col_key
        subcols = [col for col in collections if col["data"].get("parentCollection") == col_key]
        print(f"Found {len(subcols)} subcollections of '{col_name}'")
        
        item_subcollections = {}
        def register_item_subcol(item_key, subcol_name):
            if item_key not in item_subcollections:
                item_subcollections[item_key] = set()
            item_subcollections[item_key].add(subcol_name)
            
        all_targets = [{"key": col_key, "name": col_name, "is_sub": False}]
        for sc in subcols:
            all_targets.append({"key": sc["key"], "name": sc["data"]["name"], "is_sub": True})
            
        item_details = {}
        for target in all_targets:
            print(f"Fetching items for collection/subcollection '{target['name']}'...")
            start = 0
            limit = 100
            while True:
                chunk = zot.collection_items(target["key"], limit=limit, start=start)
                if not chunk:
                    break
                for item in chunk:
                    if item["data"].get("itemType") in ["attachment", "note"]:
                        continue
                    item_key = item["key"]
                    item_details[item_key] = item
                    if target["is_sub"]:
                        register_item_subcol(item_key, target["name"])
                if len(chunk) < limit:
                    break
                start += limit
                
        parent_items = list(item_details.values())
        uploaded_metadata = []
        uploaded_count = 0
        
        print(f"Processing {len(parent_items)} Zotero items...")
        for idx, item in enumerate(parent_items, 1):
            parent_key = item["key"]
            title = item["data"].get("title", "")
            date = item["data"].get("date", "")
            type_name = item["data"].get("itemType", "document")
            
            cit_key = get_zotero_citation_key(zot, item)
            target_title = cit_key
            
            is_in_mapping = parent_key in existing_keys
            is_in_nlm = (target_title in nlm_titles) or (parent_key in nlm_key_map)
            matched_nlm_title = target_title if target_title in nlm_titles else nlm_key_map.get(parent_key, target_title)
            
            is_already_uploaded = is_in_mapping or is_in_nlm
            
            if is_already_uploaded:
                print(f"[{idx}/{len(parent_items)}] '{matched_nlm_title}' (Key: {parent_key}) already uploaded. Verifying/adding labels...")
                labels_to_apply = item_subcollections.get(parent_key, set())
                if not labels_to_apply:
                    labels_to_apply = {col_name}
                for label in labels_to_apply:
                    label_source(notebook_id, matched_nlm_title, label)
                if parent_key not in existing_keys:
                    uploaded_metadata.append({
                        "author": cit_key.split("-")[0] if "-" in cit_key else "Unknown",
                        "date": date,
                        "zotero_key": parent_key,
                        "title": title,
                        "itemtype": type_name
                    })
                    existing_keys.add(parent_key)
            else:
                children = zot.children(parent_key)
                attachments = []
                for child in children:
                    c_data = child["data"]
                    if c_data.get("itemType") == "attachment" and c_data.get("contentType") in ["application/pdf", "text/html"]:
                        attachments.append(child)
                        
                if attachments:
                    att = attachments[0]
                    att_key = att["key"]
                    content_type = att["data"].get("contentType")
                    ext = ".html" if content_type == "text/html" else ".pdf"
                    filename = f"{cit_key}{ext}"
                    
                    print(f"[{idx}/{len(parent_items)}] Downloading attachment '{filename}' for '{target_title}'...")
                    current_dir = os.path.abspath(os.getcwd())
                    local_path = os.path.join(current_dir, filename)
                    upload_path = None
                    try:
                        zot.dump(att_key, local_path)
                        upload_path = local_path
                        is_html = content_type == "text/html"
                        
                        if is_html:
                            pdf_path = os.path.join(current_dir, f"{cit_key}.pdf")
                            if convert_html_to_pdf(local_path, pdf_path):
                                upload_path = pdf_path
                            else:
                                print(f"Warning: HTML to PDF conversion failed for {filename}. Falling back to original HTML.")
                                upload_path = local_path
                                
                        file_size = os.path.getsize(upload_path)
                        if file_size > 25 * 1024 * 1024:
                            print(f"Downloaded file is too large: {file_size / (1024*1024):.1f}MB (limit: 25MB). Resizing...")
                            resized_path = upload_path + ".resized.pdf"
                            if attempt_resize_pdf(upload_path, resized_path):
                                if upload_path != local_path and os.path.exists(upload_path):
                                    os.remove(upload_path)
                                upload_path = resized_path
                            else:
                                print("Warning: Could not resize PDF to fit 25MB constraint.")
                                upload_path = None
                        if not upload_path:
                            print(f"Error: File is too large. Skipping {target_title}.")
                            all_failed_items.append(f"{target_title} (File exceeds size limit)")
                            if os.path.exists(local_path):
                                os.remove(local_path)
                            continue
                            
                        print(f"Uploading '{target_title}' directly using Zotero Citation Key to NotebookLM...")
                        cmd_upload = [
                            "uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "source", "add",
                            notebook_id,
                            "--file", upload_path,
                            "--title", target_title,
                            "--wait"
                        ]
                        res_upload = subprocess.run(cmd_upload, capture_output=True, text=True, encoding="utf-8")
                        
                        # Cleanup
                        if os.path.exists(local_path):
                            os.remove(local_path)
                        if upload_path != local_path and os.path.exists(upload_path):
                            os.remove(upload_path)
                        resized_temp = upload_path + ".resized.pdf" if upload_path else ""
                        if resized_temp and os.path.exists(resized_temp):
                            os.remove(resized_temp)
                            
                        if res_upload.returncode == 0:
                            print(f"Successfully uploaded: {target_title}")
                            nlm_titles.add(target_title)
                            nlm_key_map[parent_key] = target_title
                            
                            labels_to_apply = item_subcollections.get(parent_key, set())
                            if not labels_to_apply:
                                labels_to_apply = {col_name}
                            for label in labels_to_apply:
                                label_source(notebook_id, target_title, label)
                                
                            uploaded_count += 1
                            uploaded_metadata.append({
                                "author": cit_key.split("-")[0] if "-" in cit_key else "Unknown",
                                "date": date,
                                "zotero_key": parent_key,
                                "title": title,
                                "itemtype": type_name
                            })
                            existing_keys.add(parent_key)
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
                        if upload_path and upload_path != local_path and os.path.exists(upload_path):
                            try:
                                os.remove(upload_path)
                            except:
                                pass
                        all_failed_items.append(f"{target_title} (Download/process error)")
                else:
                    print(f"[{idx}/{len(parent_items)}] Skipping '{target_title}' (No PDF or HTML attachment found).")

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
            
            for out_fname in ["_zotero_uploaded_items.json", "zotero_uploaded_items.json"]:
                with open(out_fname, "w", encoding="utf-8") as f:
                    json.dump(mapping_data, f, indent=2, ensure_ascii=False)
            print(f"Recorded metadata for {len(uploaded_metadata)} items in tracking JSON files.")

    print("\nSynchronization complete.")
    if all_failed_items:
        print("\n" + "="*80)
        print("ALERT: The following sources in the Zotero collection were ignored or not successfully uploaded:")
        for item in all_failed_items:
            print(f" - {item}")
        print("="*80 + "\n")

if __name__ == "__main__":
    main()
