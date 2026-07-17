import subprocess
import json
import sqlite3
import shutil
import os
import re
import sys
import argparse
import urllib.request
import urllib.parse
import ssl

# Ignore SSL verification for ease of web access
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def get_default_zotero_paths():
    home = os.path.expanduser("~")
    db_path = os.path.join(home, "Zotero", "zotero.sqlite")
    return db_path

def get_xml(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            return r.read().decode('utf-8', errors='ignore')
    except:
        return None

def crawl_sitemaps(domain):
    sitemap_dates = {}
    sitemap_urls = [
        f"{domain}/sitemap.xml",
        f"{domain}/sitemap_index.xml",
        f"{domain}/wp-sitemap.xml"
    ]
    for s_url in sitemap_urls:
        content = get_xml(s_url)
        if not content:
            continue
            
        # If it's a sitemap index, crawl the sub-sitemaps
        sub_sitemaps = re.findall(r'<loc>(http[s]?://[^<]+)</loc>', content)
        if "sitemapindex" in content and sub_sitemaps:
            for sub_url in sub_sitemaps:
                sub_content = get_xml(sub_url)
                if sub_content:
                    parse_sitemap_urls(sub_content, sitemap_dates)
            break
        else:
            parse_sitemap_urls(content, sitemap_dates)
            break
    return sitemap_dates

def parse_sitemap_urls(xml_content, sitemap_dates):
    url_blocks = re.findall(r'<url>.*?</url>', xml_content, re.DOTALL)
    for block in url_blocks:
        loc_match = re.search(r'<loc>(.*?)</loc>', block)
        lastmod_match = re.search(r'<lastmod>(.*?)</lastmod>', block)
        if loc_match and lastmod_match:
            url_clean = loc_match.group(1).strip()
            lastmod_val = lastmod_match.group(1).strip()
            year_match = re.match(r'^(\d{4})', lastmod_val)
            if year_match:
                sitemap_dates[url_clean] = year_match.group(1)

def get_page_meta_date(url):
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            html = r.read().decode('utf-8', errors='ignore')
        meta_tags = re.findall(r'<meta[^>]+>', html, re.I)
        for tag in meta_tags:
            prop_match = re.search(r'(?:property|name|itemprop)=["\']([^"\']*(?:date|created|modified|issued)[^"\']*)["\']', tag, re.I)
            if prop_match:
                content_match = re.search(r'content=["\']([^"\']+)["\']', tag, re.I)
                if content_match:
                    content_val = content_match.group(1).strip()
                    year_match = re.search(r'\b(20\d{2}|19\d{2})\b', content_val)
                    if year_match:
                        return year_match.group(1)
    except:
        pass
    return None

acronyms_file = "org_acronyms.json"

def load_acronyms():
    if os.path.exists(acronyms_file):
        try:
            with open(acronyms_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load acronyms file: {e}")
    # Default mappings if file doesn't exist
    default_acros = {
        "museums association of saskatchewan": "MAS",
        "university of saskatchewan art galleries and collection": "USAGC",
        "collectiveaccess": "CA",
        "collectiveaccess support": "CAS",
        "f.t. hill museum": "FTHM",
        "moosejawtoday.com": "MJT"
    }
    save_acronyms(default_acros)
    return default_acros

def save_acronyms(acros):
    try:
        with open(acronyms_file, "w", encoding="utf-8") as f:
            json.dump(acros, f, indent=2)
    except Exception as e:
        print(f"Warning: Failed to save acronyms file: {e}")

org_acronyms = load_acronyms()

def generate_acronym(name):
    # Strip common domain extensions
    clean_name = re.sub(r'\.(?:com|org|net|ca|edu|gov|php)\b', '', name, flags=re.I)
    
    # Check if the name contains spaces/hyphens/dots
    words = [w for w in re.split(r'[\s\-.]+', clean_name) if w]
    if len(words) > 1:
        acro_words = []
        for w in words:
            if w.lower() not in ["of", "and", "for", "the", "a", "an", "on", "in", "at", "by", "with", "to", "from"]:
                caps = [c for c in w if c.isupper()]
                if caps:
                    acro_words.append("".join(caps))
                elif w[0].isalpha():
                    acro_words.append(w[0].upper())
        if acro_words:
            return "".join(acro_words)
            
    # Single word - check camelCase
    caps = [c for c in clean_name if c.isupper()]
    if len(caps) >= 2:
        return "".join(caps)
        
    return re.sub(r'[^a-zA-Z0-9]', '', clean_name)[:4].upper()

def resolve_author(item_id, title, url, cursor):
    cursor.execute("""
        SELECT c.firstName, c.lastName, c.fieldMode
        FROM itemCreators ic
        JOIN creators c ON ic.creatorID = c.creatorID
        WHERE ic.itemID = ?
        ORDER BY ic.orderIndex
    """, (item_id,))
    creators = cursor.fetchall()
    
    if creators:
        first_creator = creators[0]
        first_name, last_name, mode = first_creator
        if mode == 1:
            name = last_name or first_name
            name_lower = name.lower().strip()
            if name_lower in org_acronyms:
                return org_acronyms[name_lower]
            
            # Generate acronym
            acro = generate_acronym(name)
            org_acronyms[name_lower] = acro
            save_acronyms(org_acronyms)
            return acro
        else:
            return last_name.strip()
            
    title_lower = title.lower()
    if "collectiveaccess" in title_lower:
        return "CA"
    if url and ("saskmuseums.org" in url or "– mas" in title_lower):
        return "MAS"
    return "MAS"

def get_url_slug(url_str):
    if not url_str:
        return ""
    path = urllib.parse.urlparse(url_str).path
    segments = [s for s in path.split('/') if s]
    if not segments:
        return ""
    last_seg = segments[-1]
    last_seg = os.path.splitext(last_seg)[0]
    return re.sub(r'[^a-zA-Z0-9]', '', last_seg).lower()

def get_normalized_title(title_str):
    if not title_str:
        return ""
    return re.sub(r'[^a-zA-Z0-9]', '', title_str).lower()

def main():
    parser = argparse.ArgumentParser(description="Calculate citation keys, retrieve missing dates, update Zotero database, and rename sources in NotebookLM.")
    parser.add_argument("--collection-id", type=int, required=True, help="Zotero Collection ID")
    parser.add_argument("--notebook-name", type=str, required=True, help="NotebookLM Notebook Name")
    parser.add_argument("--zotero-db", type=str, default=None, help="Path to zotero.sqlite")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing them")
    
    args = parser.parse_args()
    
    zotero_db = args.zotero_db or get_default_zotero_paths()
    if not os.path.exists(zotero_db):
        print(f"Error: Zotero database not found at {zotero_db}")
        sys.exit(1)
        
    temp_db = "zotero_citation_temp.sqlite"
    shutil.copy2(zotero_db, temp_db)
    
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    
    # Get items in collection
    cursor.execute("""
        SELECT i.itemID, i.key, t.typeName
        FROM collectionItems ci
        JOIN items i ON ci.itemID = i.itemID
        JOIN itemTypes t ON i.itemTypeID = t.itemTypeID
        WHERE ci.collectionID = ?
    """, (args.collection_id,))
    zotero_items = cursor.fetchall()
    
    if not zotero_items:
        print(f"No items found in collection ID {args.collection_id}.")
        conn.close()
        os.remove(temp_db)
        sys.exit(1)
        
    print(f"Found {len(zotero_items)} items in collection.")
    
    def get_field_val(item_id, field_name):
        cursor.execute("""
            SELECT v.value
            FROM itemData id
            JOIN fields f ON id.fieldID = f.fieldID
            JOIN itemDataValues v ON id.valueID = v.valueID
            WHERE id.itemID = ? AND f.fieldName = ?
        """, (item_id, field_name))
        row = cursor.fetchone()
        return row[0] if row else None

    # Identify domains to crawl sitemaps for
    domains_to_crawl = set()
    for item_id, key, _ in zotero_items:
        url = get_field_val(item_id, 'url')
        if url:
            parsed = urllib.parse.urlparse(url)
            domain = f"{parsed.scheme}://{parsed.netloc}"
            domains_to_crawl.add(domain)
            
    # Crawl sitemaps
    sitemap_dates = {}
    for dom in domains_to_crawl:
        print(f"Crawling sitemaps for: {dom}...")
        dates = crawl_sitemaps(dom)
        sitemap_dates.update(dates)
        
    # Calculate updates
    proposed_updates = []
    for item_id, key, _ in zotero_items:
        title = get_field_val(item_id, 'title')
        url = get_field_val(item_id, 'url')
        date_val = get_field_val(item_id, 'date')
        
        # Determine year
        year = None
        if date_val:
            m = re.search(r'\b(20\d{2}|19\d{2})\b', date_val)
            if m:
                year = m.group(1)
                
        needs_date_update = False
        if not year:
            # Check sitemap
            if url and url.strip() in sitemap_dates:
                year = sitemap_dates[url.strip()]
                needs_date_update = True
            # Check meta
            if not year:
                year = get_page_meta_date(url)
                if year:
                    needs_date_update = True
            if not year:
                year = "ND"
                
        author = resolve_author(item_id, title, url, cursor)
        cit_key = f"{author}-{year}-{key}"
        
        proposed_updates.append({
            "itemID": item_id,
            "key": key,
            "title": title,
            "url": url,
            "new_date": year if needs_date_update else None,
            "new_cit_key": cit_key
        })
        
    conn.close()
    os.remove(temp_db)
    
    # ----------------------------------------------------
    # Execute Database Writes
    # ----------------------------------------------------
    if args.dry_run:
        print("\n[DRY RUN] Proposed Updates:")
        for u in proposed_updates:
            print(f"  Item {u['key']}: Date={u['new_date'] or 'Unchanged'}, CitationKey={u['new_cit_key']} ('{u['title'][:40]}')")
    else:
        # Create Backup
        backup_db = zotero_db + ".pre_update.bak"
        print(f"\nBacking up Zotero database to {backup_db}...")
        shutil.copyfile(zotero_db, backup_db)
        
        print("Writing to Zotero database...")
        conn = sqlite3.connect(zotero_db)
        cursor = conn.cursor()
        
        def set_item_field_write(item_id, field_id, value_str):
            cursor.execute("INSERT OR IGNORE INTO itemDataValues (value) VALUES (?)", (value_str,))
            cursor.execute("SELECT valueID FROM itemDataValues WHERE value = ?", (value_str,))
            value_id = cursor.fetchone()[0]
            cursor.execute("SELECT valueID FROM itemData WHERE itemID = ? AND fieldID = ?", (item_id, field_id))
            exist_row = cursor.fetchone()
            if exist_row:
                cursor.execute("UPDATE itemData SET valueID = ? WHERE itemID = ? AND fieldID = ?", (value_id, item_id, field_id))
            else:
                cursor.execute("INSERT INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)", (item_id, field_id, value_id))
                
        for u in proposed_updates:
            item_id = u["itemID"]
            if u["new_date"]:
                set_item_field_write(item_id, 6, str(u["new_date"]))
            set_item_field_write(item_id, 64, u["new_cit_key"])
            
        conn.commit()
        conn.close()
        print("Zotero database updated successfully.")
        
    # ----------------------------------------------------
    # NotebookLM Renaming
    # ----------------------------------------------------
    print("\nFetching NotebookLM sources...")
    # List notebooks
    res_list = subprocess.run(["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "list", "notebooks"], capture_output=True, text=True, encoding="utf-8")
    notebook_id = None
    if res_list.returncode == 0:
        try:
            notebooks = json.loads(res_list.stdout)
            for nb in notebooks:
                if nb.get("title") == args.notebook_name:
                    notebook_id = nb.get("id")
                    break
        except:
            pass
            
    if not notebook_id:
        print(f"Error: Notebook '{args.notebook_name}' not found.")
        sys.exit(1)
        
    res_sources = subprocess.run(["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "list", "sources", notebook_id, "--json"], capture_output=True, text=True, encoding="utf-8")
    if res_sources.returncode != 0:
        print("Error: Could not retrieve sources from notebook.")
        sys.exit(1)
        
    try:
        notebook_sources = json.loads(res_sources.stdout)
    except:
        print("Error parsing sources list.")
        sys.exit(1)
        
    print(f"Matching and renaming {len(notebook_sources)} sources in NotebookLM...")
    for src in notebook_sources:
        s_id = src["id"]
        s_title = src["title"]
        s_url = src.get("url")
        
        matched_zotero = None
        s_slug = get_url_slug(s_url)
        if s_slug:
            for u in proposed_updates:
                z_slug = get_url_slug(u["url"])
                if z_slug and z_slug == s_slug:
                    matched_zotero = u
                    break
                    
        if not matched_zotero:
            s_norm_title = get_normalized_title(s_title)
            for u in proposed_updates:
                z_norm_title = get_normalized_title(u["title"])
                if z_norm_title and z_norm_title == s_norm_title:
                    matched_zotero = u
                    break
                    
        if matched_zotero:
            cit_key = matched_zotero["new_cit_key"]
            if s_title != cit_key:
                print(f"  Renaming '{s_title}' to '{cit_key}'...")
                if not args.dry_run:
                    subprocess.run(["uvx", "--link-mode=copy", "--from", "notebooklm-mcp-cli", "nlm", "source", "rename", "-n", notebook_id, s_id, cit_key], check=True)
            else:
                print(f"  Source '{s_title}' is already correct.")
        else:
            print(f"  Could not match source: '{s_title}'")
            
    # ----------------------------------------------------
    # Rebuild Mapping JSON
    # ----------------------------------------------------
    if not args.dry_run:
        print("\nRebuilding zotero_uploaded_items.json mapping registry...")
        relink_script = os.path.join(os.path.dirname(__file__), "relink_items.py")
        if os.path.exists(relink_script):
            subprocess.run(["python", relink_script, "--notebook-name", args.notebook_name], check=True)

if __name__ == "__main__":
    main()
