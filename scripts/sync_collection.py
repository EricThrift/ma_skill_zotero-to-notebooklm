import subprocess
import json
import sqlite3
import shutil
import os
import re
import sys
import argparse

def get_default_zotero_paths():
    home = os.path.expanduser("~")
    db_path = os.path.join(home, "Zotero", "zotero.sqlite")
    storage_path = os.path.join(home, "Zotero", "storage")
    return db_path, storage_path

def label_source(notebook_id, source_title, collection_name):
    # 1. Get or create label ID
    print(f"Finding or creating label for collection '{collection_name}'...")
    label_id = None
    
    # List labels
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
        
        # List labels again to get the ID
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
    # Attempt 1: Native pypdf compression if installed
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
        
    # Attempt 2: Ghostscript CLI if available
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

def get_zotero_text(att_folder):
    cache_path = os.path.join(att_folder, ".zotero-ft-cache")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return f.read(), ".zotero-ft-cache"
        except Exception as e:
            print(f"Error reading Zotero cache {cache_path}: {e}")
            
    unproc_path = os.path.join(att_folder, ".zotero-ft-unprocessed")
    if os.path.exists(unproc_path):
        try:
            with open(unproc_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "text" in data:
                    return data["text"], ".zotero-ft-unprocessed"
        except Exception as e:
            print(f"Error reading Zotero unprocessed {unproc_path}: {e}")
            
    return None, None

def main():
    parser = argparse.ArgumentParser(description="Synchronize collections from Zotero to NotebookLM")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Sync all previously uploaded collections")
    group.add_argument("--collection-name", type=str, help="Sync a specific collection by its Zotero name")
    group.add_argument("--list", action="store_true", help="List currently synchronized collections")
    
    parser.add_argument("--zotero-db", type=str, default=None, help="Path to zotero.sqlite")
    parser.add_argument("--zotero-storage", type=str, default=None, help="Path to Zotero storage directory")
    
    args = parser.parse_args()
    
    default_db, default_storage = get_default_zotero_paths()
    zotero_db = args.zotero_db or default_db
    zotero_storage = args.zotero_storage or default_storage
    
    output_file = "zotero_uploaded_items.json"
    
    # 1. Handle Listing
    if args.list:
        if not os.path.exists(output_file):
            print("No synchronization records found in zotero_uploaded_items.json.")
            return
        with open(output_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        notebook = data.get("target_notebook", {})
        print(f"Target NotebookLM Notebook: {notebook.get('name')} (ID: {notebook.get('notebook_id')})")
        print("\nSynchronized Zotero Collections:")
        for lib in data.get("source_libraries", []):
            print(f"Group Library: {lib.get('name')} (groupID: {lib.get('groupID')})")
            for col in lib.get("collections", []):
                print(f"  - Collection: {col.get('name')} (key: {col.get('collection_key')}) - {len(col.get('uploaded_items', []))} items")
        return
 
    # Check Zotero DB existence
    if not os.path.exists(zotero_db):
        print(f"Error: Zotero database not found at {zotero_db}")
        sys.exit(1)
        
    # Check NotebookLM CLI auth
    print("Checking NotebookLM connection...")
    res_info = subprocess.run(["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "doctor"], capture_output=True, text=True, encoding="utf-8")
    if res_info.returncode != 0:
        print("Error: NotebookLM CLI is not authenticated. Run 'nlm login' first.")
        print(res_info.stderr)
        sys.exit(1)

    # Load existing mapping
    mapping_data = {
        "target_notebook": {
            "notebook_id": "PLACEHOLDER_NOTEBOOK_ID",
            "name": "Museum Catalogues"
        },
        "source_libraries": []
    }
    if os.path.exists(output_file):
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                mapping_data = json.load(f)
        except Exception as e:
            print(f"Warning: Could not read {output_file}: {e}")

    # Resolve Notebook ID if placeholder or missing
    notebook_name = mapping_data["target_notebook"].get("name", "Museum Catalogues")
    notebook_id = mapping_data["target_notebook"].get("notebook_id")
    if not notebook_id or notebook_id == "PLACEHOLDER_NOTEBOOK_ID":
        print(f"Resolving Notebook ID for '{notebook_name}'...")
        notebook_id = resolve_notebook_id(notebook_name)
        if not notebook_id:
            print(f"Error: Could not find or resolve NotebookLM notebook named '{notebook_name}'.")
            sys.exit(1)
        mapping_data["target_notebook"]["notebook_id"] = notebook_id
        # Save resolved ID immediately
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(mapping_data, f, indent=2, ensure_ascii=False)

    print(f"Target Notebook ID: {notebook_id}")

    # 2. Open SQLite temp copy
    temp_db = "zotero_sync_temp.sqlite"
    shutil.copy2(zotero_db, temp_db)
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    collections_to_sync = []
    
    # 3. Determine which collections to sync
    if args.collection_name:
        # Search collection by name in Zotero DB
        cursor.execute("""
            SELECT collections.collectionID, groups.groupID, groups.name, collections.key, collections.collectionName
            FROM collections
            JOIN groups ON collections.libraryID = groups.libraryID
            WHERE collections.collectionName = ?
        """, (args.collection_name,))
        row = cursor.fetchone()
        if not row:
            # Personal library fallback
            cursor.execute("SELECT collectionID, key, collectionName FROM collections WHERE collectionName = ?", (args.collection_name,))
            p_row = cursor.fetchone()
            if p_row:
                col_id, col_key, col_name = p_row
                group_id = 0
                group_name = "My Library"
            else:
                print(f"Error: Collection '{args.collection_name}' not found in Zotero database.")
                conn.close()
                os.remove(temp_db)
                sys.exit(1)
        else:
            col_id, group_id, group_name, col_key, col_name = row
            
        collections_to_sync.append({
            "collection_id": col_id,
            "collection_key": col_key,
            "collection_name": col_name,
            "groupID": group_id,
            "group_name": group_name
        })
    else:  # --all
        # Get collection keys from mapping file
        mapped_collections = []
        for lib in mapping_data.get("source_libraries", []):
            for col in lib.get("collections", []):
                mapped_collections.append((col.get("collection_key"), lib.get("groupID")))
                
        if not mapped_collections:
            print("No collections registered in zotero_uploaded_items.json to sync.")
            conn.close()
            os.remove(temp_db)
            return
            
        for col_key, group_id in mapped_collections:
            cursor.execute("""
                SELECT collections.collectionID, collections.collectionName, groups.name
                FROM collections
                JOIN groups ON collections.libraryID = groups.libraryID
                WHERE collections.key = ? AND groups.groupID = ?
            """, (col_key, group_id))
            row = cursor.fetchone()
            if row:
                col_id, col_name, group_name = row
                collections_to_sync.append({
                    "collection_id": col_id,
                    "collection_key": col_key,
                    "collection_name": col_name,
                    "groupID": group_id,
                    "group_name": group_name
                })
            else:
                # Personal library fallback
                cursor.execute("SELECT collectionID, collectionName FROM collections WHERE key = ?", (col_key,))
                p_row = cursor.fetchone()
                if p_row:
                    col_id, col_name = p_row
                    collections_to_sync.append({
                        "collection_id": col_id,
                        "collection_key": col_key,
                        "collection_name": col_name,
                        "groupID": 0,
                        "group_name": "My Library"
                    })

    all_failed_items = []

    # 4. Sync each collection
    for col_info in collections_to_sync:
        col_id = col_info["collection_id"]
        col_key = col_info["collection_key"]
        col_name = col_info["collection_name"]
        group_id = col_info["groupID"]
        group_name = col_info["group_name"]
        
        print(f"\nSyncing collection '{col_name}' ({col_key}) from group '{group_name}'...")
        
        # Get existing keys in mapping
        existing_keys = set()
        lib_entry = None
        for lib in mapping_data.get("source_libraries", []):
            if lib.get("groupID") == group_id:
                lib_entry = lib
                break
        if lib_entry:
            col_entry = None
            for col in lib_entry.get("collections", []):
                if col.get("collection_key") == col_key:
                    col_entry = col
                    break
            if col_entry:
                existing_keys = {item["zotero_key"] for item in col_entry.get("uploaded_items", [])}

        # Query all items in this collection from Zotero
        cursor.execute("""
            SELECT items.itemID, items.key, itemTypes.typeName
            FROM collectionItems
            JOIN items ON collectionItems.itemID = items.itemID
            JOIN itemTypes ON items.itemTypeID = itemTypes.itemTypeID
            WHERE collectionItems.collectionID = ?
              AND itemTypes.typeName NOT IN ('attachment', 'note')
        """, (col_id,))
        parent_items = cursor.fetchall()
        
        to_upload = []
        for parent_id, parent_key, type_name in parent_items:
            if parent_key in existing_keys:
                continue # Already synchronized
                
            # Title
            cursor.execute("""
                SELECT itemDataValues.value
                FROM itemData
                JOIN fields ON itemData.fieldID = fields.fieldID
                JOIN itemDataValues ON itemData.valueID = itemDataValues.valueID
                WHERE itemData.itemID = ? AND fields.fieldName = 'title'
            """, (parent_id,))
            title_row = cursor.fetchone()
            title = title_row[0] if title_row else ""

            # Year
            cursor.execute("""
                SELECT itemDataValues.value
                FROM itemData
                JOIN fields ON itemData.fieldID = fields.fieldID
                JOIN itemDataValues ON itemData.valueID = itemDataValues.valueID
                WHERE itemData.itemID = ? AND fields.fieldName = 'date'
            """, (parent_id,))
            date_row = cursor.fetchone()
            date = date_row[0] if date_row else ""
            year = "Unknown"
            if date:
                m = re.search(r'\b(19|20)\d{2}\b', date)
                if m:
                    year = m.group(0)
                else:
                    year = date[:4]

            # Author
            cursor.execute("""
                SELECT creators.lastName
                FROM itemCreators
                JOIN creators ON itemCreators.creatorID = creators.creatorID
                WHERE itemCreators.itemID = ?
                ORDER BY itemCreators.orderIndex
                LIMIT 1
            """, (parent_id,))
            author_row = cursor.fetchone()
            author = author_row[0] if author_row else "Unknown"

            # Attachment path
            cursor.execute("""
                SELECT items.key, itemAttachments.path
                FROM items
                JOIN itemAttachments ON items.itemID = itemAttachments.itemID
                WHERE itemAttachments.parentItemID = ? AND itemAttachments.contentType = 'application/pdf'
            """, (parent_id,))
            attachments = cursor.fetchall()
            
            for att_key, att_path in attachments:
                filename = att_path
                if att_path and att_path.startswith("storage:"):
                    filename = att_path[8:]
                
                local_path = os.path.join(zotero_storage, att_key, filename)
                if os.path.exists(local_path):
                    target_title = f"{author}-{year}-{parent_key}"
                    to_upload.append((local_path, target_title, parent_key, author, date, type_name, title))

        if not to_upload:
            print(f"Collection '{col_name}' is already up-to-date.")
            continue
            
        print(f"Uploading {len(to_upload)} new PDF attachments to NotebookLM...")
        uploaded_count = 0
        uploaded_metadata = []
        
        for file_path, target_title, parent_key, author, date, type_name, title in to_upload:
            print(f"[{uploaded_count+len(all_failed_items)+1}/{len(to_upload)}] Processing '{target_title}'...")
            
            file_size = os.path.getsize(file_path)
            upload_path = file_path
            temp_txt_path = None
            
            # Check size constraints
            if file_size > 25 * 1024 * 1024:
                print(f"Source file is too large: {file_size / (1024*1024):.1f}MB (limit: 25MB)")
                
                resized_path = file_path + ".resized.pdf"
                print("Attempting to resize PDF to fit size limit...")
                if attempt_resize_pdf(file_path, resized_path):
                    print("Successfully resized PDF!")
                    upload_path = resized_path
                else:
                    print("Could not resize PDF. Attempting to retrieve full-text content from Zotero storage...")
                    att_folder = os.path.dirname(file_path)
                    extracted_text, source_file = get_zotero_text(att_folder)
                    if extracted_text:
                        print(f"Found Zotero extracted text in {source_file}!")
                        temp_txt_path = os.path.join(os.path.dirname(output_file) or ".", f"{target_title}.txt")
                        try:
                            with open(temp_txt_path, "w", encoding="utf-8") as f:
                                f.write(extracted_text)
                            upload_path = temp_txt_path
                        except Exception as e:
                            print(f"Error writing temporary text file: {e}")
                            upload_path = None
                    else:
                        print("Warning: No extracted full-text content found in Zotero storage directory.")
                        upload_path = None
                        
            if not upload_path:
                print(f"Error: PDF is too large and text content could not be retrieved. Skipping {target_title}.")
                all_failed_items.append(f"{target_title} (File too large, failed to resize/extract text)")
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
            
            # Clean up temp files
            if temp_txt_path and os.path.exists(temp_txt_path):
                try:
                    os.remove(temp_txt_path)
                except Exception:
                    pass
            resized_temp = file_path + ".resized.pdf"
            if os.path.exists(resized_temp):
                try:
                    os.remove(resized_temp)
                except Exception:
                    pass

            if res_upload.returncode == 0:
                print(f"Successfully uploaded: {target_title}")
                # Assign to collection label
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
                all_failed_items.append(f"{target_title} (NotebookLM upload command failed: {res_upload.stderr.strip()})")
                
        # Merge results into mapping_data
        if uploaded_metadata:
            # Find/create group entry
            lib_entry = None
            for lib in mapping_data.get("source_libraries", []):
                if lib.get("groupID") == group_id:
                    lib_entry = lib
                    break
            if not lib_entry:
                lib_entry = {
                    "groupID": group_id,
                    "name": group_name,
                    "collections": []
                }
                mapping_data["source_libraries"].append(lib_entry)
                
            # Find/create collection entry
            col_entry = None
            for col in lib_entry.get("collections", []):
                if col.get("collection_key") == col_key:
                    col_entry = col
                    break
            if not col_entry:
                col_entry = {
                    "collection_key": col_key,
                    "name": col_name,
                    "uploaded_items": []
                }
                lib_entry["collections"].append(col_entry)
                
            existing_items_dict = {item["zotero_key"]: item for item in col_entry["uploaded_items"]}
            for item in uploaded_metadata:
                existing_items_dict[item["zotero_key"]] = item
            col_entry["uploaded_items"] = list(existing_items_dict.values())
            
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(mapping_data, f, indent=2, ensure_ascii=False)
            print(f"Recorded metadata for {len(uploaded_metadata)} new items in {output_file}")

    conn.close()
    os.remove(temp_db)
    print("\nSynchronization complete.")
    
    if all_failed_items:
        print("\n" + "="*80)
        print("ALERT: The following sources in the Zotero collection were ignored or not successfully uploaded:")
        for item in all_failed_items:
            print(f" - {item}")
        print("="*80 + "\n")

if __name__ == "__main__":
    main()
