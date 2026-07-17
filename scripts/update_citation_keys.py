import subprocess
import json
import os
import re
import sys
import argparse
import urllib.request
import urllib.parse
import ssl
from datetime import datetime
from pyzotero import zotero

# Ignore SSL verification for ease of web access
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def load_credentials():
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
        print("Zotero credentials are required to interact with the Zotero Web API.")
        if not library_id:
            library_id = input("Enter Zotero Library ID (e.g. 6611671): ").strip()
        if not library_type:
            library_type = input("Enter Zotero Library Type ('group' or 'user'): ").strip().lower()
            if library_type not in ["group", "user"]:
                library_type = "group"
        if not api_key:
            api_key = input("Enter Zotero API Key: ").strip()
            
        # Write to .env
        with open(env_path, "a" if os.path.exists(env_path) else "w", encoding="utf-8") as f:
            f.write(f"\nZOTERO_LIBRARY_ID={library_id}\n")
            f.write(f"ZOTERO_LIBRARY_TYPE={library_type}\n")
            f.write(f"ZOTERO_API_KEY={api_key}\n")
        print(f"Credentials saved to {env_path}")
        
    return library_id, library_type, api_key

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
    clean_name = re.sub(r'\.(?:com|org|net|ca|edu|gov|php)\b', '', name, flags=re.I)
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
            
    caps = [c for c in clean_name if c.isupper()]
    if len(caps) >= 2:
        return "".join(caps)
        
    return re.sub(r'[^a-zA-Z0-9]', '', clean_name)[:4].upper()

def resolve_author(item, title, url):
    creators = item['data'].get('creators', [])
    if creators:
        first_creator = creators[0]
        if 'name' in first_creator:
            name = first_creator['name']
            name_lower = name.lower().strip()
            if name_lower in org_acronyms:
                return org_acronyms[name_lower]
            
            acro = generate_acronym(name)
            org_acronyms[name_lower] = acro
            save_acronyms(org_acronyms)
            return acro
        else:
            return first_creator.get('lastName', '').strip()
            
    title_lower = title.lower() if title else ""
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

def revert_last_changes(zot):
    changelog_file = "zotero_changes.json"
    if not os.path.exists(changelog_file):
        print("No Zotero changelog found. Cannot revert.")
        return
        
    try:
        with open(changelog_file, "r", encoding="utf-8") as f:
            log_entries = json.load(f)
    except Exception as e:
        print(f"Error loading changelog: {e}")
        return
        
    if not log_entries:
        print("No changes found in Zotero changelog.")
        return
        
    last_batch = log_entries.pop()
    print(f"Reverting batch from {last_batch['timestamp']}...")
    
    reverted_items = []
    for change in last_batch["items"]:
        key = change["key"]
        old_date = change["old_date"]
        old_cit_key = change["old_cit_key"]
        
        print(f"Preparing revert for item {key} ('{change['title'][:40]}')...")
        try:
            item = zot.item(key)
            updated = False
            if old_date is not None:
                print(f"  Restoring Date: '{old_date}' (was '{change['new_date']}')")
                item['data']['date'] = old_date
                updated = True
            if old_cit_key is not None:
                print(f"  Restoring citationKey: '{old_cit_key}' (was '{change['new_cit_key']}')")
                item['data']['citationKey'] = old_cit_key
                updated = True
                
            if updated:
                reverted_items.append(item)
        except Exception as e:
            print(f"  Failed to retrieve item {key}: {e}")
            
    if reverted_items:
        try:
            zot.update_items(reverted_items)
            print(f"\nSuccessfully reverted {len(reverted_items)} items in Zotero library.")
        except Exception as e:
            print(f"Failed to update Zotero items: {e}")
            return
            
    with open(changelog_file, "w", encoding="utf-8") as f:
        json.dump(log_entries, f, indent=2)

def main():
    parser = argparse.ArgumentParser(description="Calculate citation keys, retrieve missing dates, update Zotero database, and rename sources in NotebookLM.")
    parser.add_argument("--collection-id", type=str, default=None, help="Zotero Collection ID (required unless reverting)")
    parser.add_argument("--notebook-name", type=str, default=None, help="NotebookLM Notebook Name (required unless reverting)")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing them")
    parser.add_argument("--revert", action="store_true", help="Revert the most recent Zotero updates batch")
    
    args = parser.parse_args()
    
    # Load credentials
    library_id, library_type, api_key = load_credentials()
    zot = zotero.Zotero(library_id, library_type, api_key)
    
    if args.revert:
        revert_last_changes(zot)
        sys.exit(0)
        
    if not args.collection_id or not args.notebook_name:
        parser.print_help()
        print("\nError: --collection-id and --notebook-name are required unless --revert is specified.")
        sys.exit(1)
        
    print(f"Fetching Zotero items in collection ID {args.collection_id}...")
    try:
        zotero_items = zot.collection_items(args.collection_id)
    except Exception as e:
        print(f"Error fetching collection items from API: {e}")
        sys.exit(1)
        
    # Filter parent items (ignore attachments/notes)
    parent_items = [item for item in zotero_items if item['data'].get('itemType') not in ['attachment', 'note']]
    
    if not parent_items:
        print(f"No parent items found in collection ID {args.collection_id}.")
        sys.exit(1)
        
    print(f"Found {len(parent_items)} parent items in collection.")
    
    # Identify domains to crawl sitemaps for
    domains_to_crawl = set()
    for item in parent_items:
        url = item['data'].get('url')
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
    log_items = []
    items_to_update = []
    
    for item in parent_items:
        key = item['key']
        title = item['data'].get('title', '')
        url = item['data'].get('url', '')
        date_val = item['data'].get('date', '')
        current_cit_key = item['data'].get('citationKey', '')
        
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
                
        author = resolve_author(item, title, url)
        cit_key = f"{author}-{year}-{key}"
        
        date_changed = needs_date_update and (str(year) != str(date_val))
        key_changed = (cit_key != current_cit_key)
        
        if date_changed or key_changed:
            old_date = date_val if date_changed else None
            old_cit_key = current_cit_key if key_changed else None
            
            log_items.append({
                "key": key,
                "title": title,
                "old_date": old_date,
                "new_date": year if date_changed else None,
                "old_cit_key": old_cit_key,
                "new_cit_key": cit_key if key_changed else None
            })
            
            if date_changed:
                item['data']['date'] = str(year)
            if key_changed:
                item['data']['citationKey'] = cit_key
                
            items_to_update.append(item)
            
        proposed_updates.append({
            "key": key,
            "title": title,
            "url": url,
            "new_date": year if needs_date_update else None,
            "new_cit_key": cit_key
        })
        
    # Execute Database Writes
    if args.dry_run:
        print("\n[DRY RUN] Proposed Updates:")
        for u in proposed_updates:
            print(f"  Item {u['key']}: Date={u['new_date'] or 'Unchanged'}, CitationKey={u['new_cit_key']} ('{u['title'][:40]}')")
    else:
        if items_to_update:
            print(f"Updating {len(items_to_update)} items in Zotero via Web API...")
            try:
                zot.update_items(items_to_update)
                print("Zotero Web API update completed successfully.")
            except Exception as e:
                print(f"Error updating items in Zotero: {e}")
                sys.exit(1)
                
            # Log changes
            changelog_file = "zotero_changes.json"
            log_entries = []
            if os.path.exists(changelog_file):
                try:
                    with open(changelog_file, "r", encoding="utf-8") as f:
                        log_entries = json.load(f)
                except:
                    pass
            log_entries.append({
                "timestamp": datetime.now().isoformat(),
                "action": "update_citation_keys",
                "items": log_items
            })
            try:
                with open(changelog_file, "w", encoding="utf-8") as f:
                    json.dump(log_entries, f, indent=2)
                print(f"Log of changes written to {changelog_file}.")
            except Exception as e:
                print(f"Warning: Failed to write change log: {e}")
        else:
            print("No updates needed in Zotero library.")
            
    # ----------------------------------------------------
    # NotebookLM Renaming
    # ----------------------------------------------------
    print("\nFetching NotebookLM sources...")
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
