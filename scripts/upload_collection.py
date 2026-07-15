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
        except Exception as e:
            print(f"Error parsing labels: {e}")
            
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
    parser = argparse.ArgumentParser(description="Upload PDFs in a Zotero collection to NotebookLM")
    parser.add_argument("--collection-id", type=int, required=True, help="Zotero Collection Database ID (e.g., 858)")
    parser.add_argument("--notebook-name", type=str, required=True, help="Target NotebookLM Notebook Name")
    parser.add_argument("--zotero-db", type=str, default=None, help="Path to zotero.sqlite")
    parser.add_argument("--zotero-storage", type=str, default=None, help="Path to Zotero storage directory")
    
    args = parser.parse_args()
    
    default_db, default_storage = get_default_zotero_paths()
    zotero_db = args.zotero_db or default_db
    zotero_storage = args.zotero_storage or default_storage
    
    if not os.path.exists(zotero_db):
        print(f"Error: Zotero database not found at {zotero_db}")
        sys.exit(1)
        
    # Check if NotebookLM CLI is authenticated
    print("Checking NotebookLM connection...")
    res_info = subprocess.run(["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "doctor"], capture_output=True, text=True, encoding="utf-8")
    if res_info.returncode != 0:
        print("Error: NotebookLM CLI is not authenticated. Run 'nlm login' first.")
        print(res_info.stderr)
        sys.exit(1)

    notebook_id = None
    # Check if notebook already exists
    print("Listing existing notebooks...")
    cmd_list = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "list", "notebooks"]
    res_list = subprocess.run(cmd_list, capture_output=True, text=True, encoding="utf-8")
    if res_list.returncode == 0:
        try:
            notebooks = json.loads(res_list.stdout)
            for nb in notebooks:
                if nb.get("title") == args.notebook_name:
                    notebook_id = nb.get("id")
                    print(f"Found existing notebook '{args.notebook_name}' with ID: {notebook_id}")
                    break
        except Exception as e:
            print(f"Warning: Could not parse existing notebooks list: {e}")

    # Create if not found
    if not notebook_id:
        print(f"Creating notebook '{args.notebook_name}'...")
        cmd_create = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "create", "notebook", args.notebook_name]
        res_create = subprocess.run(cmd_create, capture_output=True, text=True, encoding="utf-8", check=True)
        try:
            data = json.loads(res_create.stdout)
            notebook_id = data.get("notebook_id") or data.get("id")
        except Exception:
            m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", res_create.stdout)
            if m:
                notebook_id = m.group(0)

    if not notebook_id:
        print("Error: Could not retrieve target notebook ID.")
        sys.exit(1)

    print(f"Target notebook ID: {notebook_id}")

    # Open SQLite Temp Copy to prevent locks
    temp_db = "zotero_mcp_temp.sqlite"
    shutil.copy2(zotero_db, temp_db)
    
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()

    # Query group details and collection name
    cursor.execute("""
        SELECT groups.groupID, groups.name, collections.key, collections.collectionName
        FROM collections
        JOIN groups ON collections.libraryID = groups.libraryID
        WHERE collections.collectionID = ?
    """, (args.collection_id,))
    group_info = cursor.fetchone()
    if group_info:
        group_id, group_name, collection_key, collection_name = group_info
    else:
        # Personal library fallback
        cursor.execute("SELECT key, collectionName FROM collections WHERE collectionID = ?", (args.collection_id,))
        col_row = cursor.fetchone()
        collection_key = col_row[0] if col_row else "Unknown"
        collection_name = col_row[1] if col_row else "My Collection"
        group_id = 0
        group_name = "My Library"

    # Query items in the collection
    cursor.execute("""
        SELECT items.itemID, items.key, itemTypes.typeName
        FROM collectionItems
        JOIN items ON collectionItems.itemID = items.itemID
        JOIN itemTypes ON items.itemTypeID = itemTypes.itemTypeID
        WHERE collectionItems.collectionID = ?
          AND itemTypes.typeName NOT IN ('attachment', 'note')
    """, (args.collection_id,))
    parent_items = cursor.fetchall()
    
    print(f"Found {len(parent_items)} parent items in collection {args.collection_id}.")
    
    to_upload = []
    for parent_id, parent_key, type_name in parent_items:
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
                
    conn.close()
    os.remove(temp_db)

    print(f"\nProcessing {len(to_upload)} PDF attachments for NotebookLM...")
    uploaded_count = 0
    uploaded_metadata = []
    failed_items = []
    
    output_file = "zotero_uploaded_items.json"
    
    for file_path, target_title, parent_key, author, date, type_name, title in to_upload:
        print(f"[{uploaded_count+len(failed_items)+1}/{len(to_upload)}] Processing '{target_title}'...")
        
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
            failed_items.append(f"{target_title} (File too large, failed to resize/extract text)")
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
            # Assign label to source in NotebookLM
            label_source(notebook_id, target_title, collection_name)
            
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
            failed_items.append(f"{target_title} (NotebookLM upload command failed: {res_upload.stderr.strip()})")

    print(f"\nSuccess! Uploaded {uploaded_count} sources to notebook '{args.notebook_name}'.")

    if uploaded_metadata:
        # Load existing metadata if file exists
        target_notebook = {
            "notebook_id": notebook_id,
            "name": args.notebook_name
        }
        source_libraries = []
        
        if os.path.exists(output_file):
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    if "target_notebook" in old_data:
                        target_notebook = old_data["target_notebook"]
                    if "source_libraries" in old_data:
                        source_libraries = old_data["source_libraries"]
                    elif "source_library" in old_data:
                        # Upgrade old schema
                        old_lib = old_data["source_library"]
                        old_items = old_data.get("uploaded_items", [])
                        source_libraries.append({
                            "groupID": old_lib.get("groupID"),
                            "name": old_lib.get("name"),
                            "collections": [
                                {
                                    "collection_key": old_lib.get("collection_key"),
                                    "name": collection_name,
                                    "uploaded_items": old_items
                                }
                            ]
                        })
            except Exception as e:
                print(f"Warning: Could not read existing {output_file}: {e}")
                
        # Find or create group entry
        lib_entry = None
        for lib in source_libraries:
            if lib.get("groupID") == group_id:
                lib_entry = lib
                break
        if not lib_entry:
            lib_entry = {
                "groupID": group_id,
                "name": group_name,
                "collections": []
            }
            source_libraries.append(lib_entry)
            
        # Find or create collection entry
        col_entry = None
        for col in lib_entry.get("collections", []):
            if col.get("collection_key") == collection_key:
                col_entry = col
                break
        if not col_entry:
            col_entry = {
                "collection_key": collection_key,
                "name": collection_name,
                "uploaded_items": []
            }
            lib_entry["collections"].append(col_entry)
            
        # Merge new uploads
        existing_items = {item["zotero_key"]: item for item in col_entry["uploaded_items"]}
        for item in uploaded_metadata:
            existing_items[item["zotero_key"]] = item
        col_entry["uploaded_items"] = list(existing_items.values())
        
        output_data = {
            "target_notebook": target_notebook,
            "source_libraries": source_libraries
        }
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        print(f"Recorded metadata for {len(uploaded_metadata)} uploaded items in {output_file}")

    if failed_items:
        print("\n" + "="*80)
        print("ALERT: The following sources in the Zotero collection were ignored or not successfully uploaded:")
        for item in failed_items:
            print(f" - {item}")
        print("="*80 + "\n")

if __name__ == "__main__":
    main()
