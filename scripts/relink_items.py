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
    return db_path

def main():
    parser = argparse.ArgumentParser(description="Relink items between Zotero and NotebookLM to rebuild metadata mapping JSON.")
    parser.add_argument("--notebook-name", type=str, required=True, help="Target NotebookLM Notebook Name")
    parser.add_argument("--zotero-db", type=str, default=None, help="Path to zotero.sqlite")
    
    args = parser.parse_args()
    
    zotero_db = args.zotero_db or get_default_zotero_paths()
    
    if not os.path.exists(zotero_db):
        print(f"Error: Zotero database not found at {zotero_db}")
        sys.exit(1)
        
    # Check NotebookLM connection
    print("Checking NotebookLM connection...")
    res_info = subprocess.run(["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "doctor"], capture_output=True, text=True, encoding="utf-8")
    if res_info.returncode != 0:
        print("Error: NotebookLM CLI is not authenticated. Run 'nlm login' first.")
        print(res_info.stderr)
        sys.exit(1)

    notebook_id = None
    # Find notebook ID by title
    print("Listing existing notebooks...")
    cmd_list = ["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "list", "notebooks"]
    res_list = subprocess.run(cmd_list, capture_output=True, text=True, encoding="utf-8")
    if res_list.returncode == 0:
        try:
            notebooks = json.loads(res_list.stdout)
            for nb in notebooks:
                if nb.get("title") == args.notebook_name:
                    notebook_id = nb.get("id")
                    print(f"Found notebook '{args.notebook_name}' with ID: {notebook_id}")
                    break
        except Exception as e:
            print(f"Error parsing notebooks list: {e}")
            sys.exit(1)
            
    if not notebook_id:
        print(f"Error: Notebook '{args.notebook_name}' not found.")
        sys.exit(1)

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
    
    # Open SQLite Temp Copy to prevent locks
    temp_db = "zotero_relink_temp.sqlite"
    shutil.copy2(zotero_db, temp_db)
    
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    
    relinked_items = []
    library_ids = set()
    collection_ids = set()
    
    for key in zotero_keys:
        # Fetch parent item details
        cursor.execute("""
            SELECT items.itemID, items.libraryID, itemTypes.typeName
            FROM items
            JOIN itemTypes ON items.itemTypeID = itemTypes.itemTypeID
            WHERE items.key = ? AND itemTypes.typeName NOT IN ('attachment', 'note')
        """, (key,))
        item_row = cursor.fetchone()
        if not item_row:
            continue
            
        item_id, library_id, type_name = item_row
        library_ids.add(library_id)
        
        # Title
        cursor.execute("""
            SELECT itemDataValues.value
            FROM itemData
            JOIN fields ON itemData.fieldID = fields.fieldID
            JOIN itemDataValues ON itemData.valueID = itemDataValues.valueID
            WHERE itemData.itemID = ? AND fields.fieldName = 'title'
        """, (item_id,))
        title_row = cursor.fetchone()
        title = title_row[0] if title_row else ""

        # Date
        cursor.execute("""
            SELECT itemDataValues.value
            FROM itemData
            JOIN fields ON itemData.fieldID = fields.fieldID
            JOIN itemDataValues ON itemData.valueID = itemDataValues.valueID
            WHERE itemData.itemID = ? AND fields.fieldName = 'date'
        """, (item_id,))
        date_row = cursor.fetchone()
        date = date_row[0] if date_row else ""

        # Author
        cursor.execute("""
            SELECT creators.lastName
            FROM itemCreators
            JOIN creators ON itemCreators.creatorID = creators.creatorID
            WHERE itemCreators.itemID = ?
            ORDER BY itemCreators.orderIndex
            LIMIT 1
        """, (item_id,))
        author_row = cursor.fetchone()
        author = author_row[0] if author_row else "Unknown"
        
        # Collections
        cursor.execute("SELECT collectionID FROM collectionItems WHERE itemID = ?", (item_id,))
        for col_row in cursor.fetchall():
            collection_ids.add(col_row[0])

        relinked_items.append({
            "author": author,
            "date": date,
            "zotero_key": key,
            "title": title,
            "itemtype": type_name
        })

    # Resolve groupID and collection_key
    group_id = 0
    group_name = "My Library"
    collection_key = "Unknown"
    
    if library_ids:
        # Take the first library ID
        lib_id = list(library_ids)[0]
        cursor.execute("SELECT groupID, name FROM groups WHERE libraryID = ?", (lib_id,))
        group_row = cursor.fetchone()
        if group_row:
            group_id, group_name = group_row
            
    if collection_ids:
        # Take the first collection ID
        col_id = list(collection_ids)[0]
        cursor.execute("SELECT key FROM collections WHERE collectionID = ?", (col_id,))
        col_row = cursor.fetchone()
        if col_row:
            collection_key = col_row[0]
            
    conn.close()
    os.remove(temp_db)
    
    output_file = "zotero_uploaded_items.json"
    output_data = {
        "source_library": {
            "groupID": group_id,
            "collection_key": collection_key,
            "name": group_name
        },
        "uploaded_items": relinked_items
    }
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
        
    print(f"Successfully relinked and wrote {len(relinked_items)} items to {output_file}")

if __name__ == "__main__":
    main()
