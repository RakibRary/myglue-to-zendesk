#!/usr/bin/env python3
"""
Zendesk MyGlue Batch Importer.
Walks a MyGlue export folder, creates a Zendesk KB article for each document,
uploads embedded images inline, and uploads attached files as article attachments
with a linked "Attached Files" section at the bottom.

Usage:
    python3 zendesk_import_myglue.py

Requires: beautifulsoup4, requests
Install:  python3 -m pip install beautifulsoup4 requests
"""

import os
import sys
import getpass
import csv
import re
import mimetypes
import html as html_lib

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing 'beautifulsoup4'. Install with:  python3 -m pip install beautifulsoup4 requests")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Missing 'requests'. Install with:  python3 -m pip install beautifulsoup4 requests")
    sys.exit(1)


DOC_FOLDER_RE = re.compile(r"^DOC-\d+-(\d+)\s+(.+)$")


def clean_subdomain(v: str) -> str:
    v = v.strip()
    for prefix in ("https://", "http://"):
        if v.lower().startswith(prefix):
            v = v[len(prefix):]
    for suffix in (".zendesk.com", ".com"):
        if v.lower().endswith(suffix):
            v = v[: -len(suffix)]
    return v.split("/")[0]


def load_docs_csv(myglue_root):
    path = os.path.join(myglue_root, "documents.csv")
    if not os.path.exists(path):
        return {}
    result = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            doc_id = (row.get("id") or "").strip()
            if doc_id:
                result[doc_id] = {
                    "name": (row.get("name") or "").strip(),
                    "archived": (row.get("archived") or "").strip().lower() == "yes",
                }
    return result


def find_doc_folders(root):
    docs_root = os.path.join(root, "documents")
    if not os.path.isdir(docs_root):
        docs_root = root
    for dirpath, dirnames, _ in os.walk(docs_root):
        prune = []
        rel = os.path.relpath(dirpath, docs_root)
        parents = [] if rel == "." else rel.split(os.sep)
        for d in dirnames:
            m = DOC_FOLDER_RE.match(d)
            if m:
                yield {
                    "doc_id": m.group(1),
                    "folder_title": m.group(2),
                    "folder_path": os.path.join(dirpath, d),
                    "parents": list(parents),
                }
                prune.append(d)
        for p in prune:
            dirnames.remove(p)


def normalize_section_name(parts):
    cleaned = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip()
        if p:
            cleaned.append(p)
    return " - ".join(cleaned)


def get_section(subdomain, auth, section_id):
    url = f"https://{subdomain}.zendesk.com/api/v2/help_center/sections/{section_id}.json"
    resp = requests.get(url, auth=auth, timeout=60)
    resp.raise_for_status()
    return resp.json()["section"]


def list_category_sections(subdomain, auth, category_id):
    sections = {}
    url = f"https://{subdomain}.zendesk.com/api/v2/help_center/categories/{category_id}/sections.json?per_page=100"
    while url:
        resp = requests.get(url, auth=auth, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        for s in data["sections"]:
            sections[s["name"].strip().lower()] = s["id"]
        url = data.get("next_page")
    return sections


def create_section_in_category(subdomain, auth, category_id, name):
    url = f"https://{subdomain}.zendesk.com/api/v2/help_center/categories/{category_id}/sections.json"
    payload = {"section": {"name": name, "locale": "en-us"}}
    resp = requests.post(url, json=payload, auth=auth, timeout=60)
    resp.raise_for_status()
    return resp.json()["section"]["id"]


def resolve_section_id(parents, root_section_id, section_cache, subdomain, auth, category_id):
    if not parents:
        return root_section_id
    name = normalize_section_name(parents)
    key = name.lower()
    if key in section_cache:
        return section_cache[key]
    print(f"   Creating section: {name}")
    new_id = create_section_in_category(subdomain, auth, category_id, name)
    section_cache[key] = new_id
    return new_id


def find_html_file(doc_folder):
    for name in os.listdir(doc_folder):
        if name.lower().endswith((".html", ".htm")):
            return os.path.join(doc_folder, name)
    return None


def find_image_file(doc_folder, src):
    parent = os.path.join(doc_folder, os.path.dirname(src))
    basename = os.path.basename(src)
    if not os.path.isdir(parent):
        return None
    exact = os.path.join(parent, basename)
    if os.path.isfile(exact):
        return exact
    for f in os.listdir(parent):
        if os.path.splitext(f)[0] == basename:
            return os.path.join(parent, f)
    return None


def create_article(subdomain, auth, section_id, title, draft):
    url = f"https://{subdomain}.zendesk.com/api/v2/help_center/en-us/sections/{section_id}/articles.json"
    payload = {
        "article": {
            "title": title or "(untitled)",
            "body": "<p>Uploading...</p>",
            "locale": "en-us",
            "draft": draft,
        }
    }
    resp = requests.post(url, json=payload, auth=auth, timeout=60)
    resp.raise_for_status()
    return resp.json()["article"]["id"]


def upload_article_file(subdomain, auth, article_id, filepath, inline):
    filename = os.path.basename(filepath)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with open(filepath, "rb") as f:
        data = f.read()
    url = f"https://{subdomain}.zendesk.com/api/v2/help_center/articles/{article_id}/attachments.json"
    files = {"file": (filename, data, mime)}
    resp = requests.post(
        url, auth=auth,
        files=files,
        data={"inline": "true" if inline else "false"},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["article_attachment"]["content_url"]


def update_article_body(subdomain, auth, article_id, body):
    url = f"https://{subdomain}.zendesk.com/api/v2/help_center/articles/{article_id}/translations/en-us.json"
    payload = {"translation": {"body": body}}
    resp = requests.put(url, json=payload, auth=auth, timeout=60)
    resp.raise_for_status()


def process_html(html_path, doc_folder, subdomain, auth, article_id):
    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    soup = BeautifulSoup(raw, "html.parser")

    for a in list(soup.find_all("a")):
        href = a.get("href", "")
        if href and not href.startswith(("http://", "https://", "mailto:", "#", "tel:")):
            local_path = os.path.join(doc_folder, href)
            if os.path.exists(local_path):
                a.unwrap()

    uploaded_cache = {}
    warns = []
    for img in soup.find_all("img"):
        src = img.get("data-src-original") or img.get("src")
        if not src:
            continue
        if src.startswith(("http://", "https://", "data:")):
            continue

        file_path = find_image_file(doc_folder, src)
        if not file_path:
            warns.append(f"image not found: {src}")
            continue

        if file_path in uploaded_cache:
            new_url = uploaded_cache[file_path]
        else:
            try:
                new_url = upload_article_file(subdomain, auth, article_id, file_path, inline=True)
                uploaded_cache[file_path] = new_url
            except Exception as e:
                warns.append(f"failed to upload {src}: {e}")
                continue

        img["src"] = new_url
        img["style"] = (img.get("style", "") + ";max-width:100%;").lstrip(";")
        if img.has_attr("data-src-original"):
            del img["data-src-original"]

    return str(soup), warns, len(uploaded_cache)


def upload_doc_attachments(myglue_root, doc_id, subdomain, auth, article_id):
    folder = os.path.join(myglue_root, "attachments", "documents", doc_id)
    if not os.path.isdir(folder):
        return [], []
    uploaded, errors = [], []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or name.startswith("."):
            continue
        try:
            url = upload_article_file(subdomain, auth, article_id, path, inline=False)
            uploaded.append((name, url))
        except Exception as e:
            errors.append(f"attachment {name} failed: {e}")
    return uploaded, errors


def build_files_section(attachments):
    if not attachments:
        return ""
    items = "".join(
        f'<li><a href="{html_lib.escape(url)}" target="_blank" rel="noopener">{html_lib.escape(name)}</a></li>'
        for name, url in attachments
    )
    return f"<hr><h3>Attached Files</h3><ul>{items}</ul>"


def process_one_doc(doc, csv_data, subdomain, auth, section_id, base_draft, myglue_root):
    doc_id = doc["doc_id"]
    folder_title = doc["folder_title"]
    doc_folder = doc["folder_path"]

    csv_row = csv_data.get(doc_id, {})
    title = csv_row.get("name") or folder_title
    archived = csv_row.get("archived", False)
    draft = base_draft or archived

    print(f"\n-> [{doc_id}] {title}")
    if doc.get("parents"):
        print(f"   Category path: {' / '.join(doc['parents'])}")
    if archived:
        print("   (archived -> forcing Draft)")

    html_path = find_html_file(doc_folder)
    if not html_path:
        print("   [SKIP] no .html file found")
        return "skip"

    try:
        article_id = create_article(subdomain, auth, section_id, title, draft)
    except Exception as e:
        print(f"   [ERROR creating article] {e}")
        return "fail"

    try:
        body, warns, img_count = process_html(html_path, doc_folder, subdomain, auth, article_id)
    except Exception as e:
        print(f"   [ERROR processing HTML] {e}")
        return "fail"

    attachments, att_errors = upload_doc_attachments(myglue_root, doc_id, subdomain, auth, article_id)
    body += build_files_section(attachments)

    for w in warns:
        print(f"   [WARN] {w}")
    for e in att_errors:
        print(f"   [WARN] {e}")
    print(f"   Inline images: {img_count}, Attachments: {len(attachments)}")

    try:
        update_article_body(subdomain, auth, article_id, body)
        print(f"   [OK] Article {article_id}")
        return "ok"
    except Exception as e:
        print(f"   [ERROR updating body] {e}")
        return "fail"


def main():
    print("=== Zendesk MyGlue Batch Importer ===\n")

    subdomain = clean_subdomain(input("Zendesk subdomain (e.g. yourcompany): "))
    email = input("Email: ").strip()
    api_token = getpass.getpass("API token (hidden): ").strip()
    section_id = input("Section ID: ").strip()
    myglue_root = input("Path to MyGlue export root folder: ").strip()
    base_draft = input("Import all as Draft? (y/N): ").strip().lower() == "y"

    if not all([subdomain, email, api_token, section_id]):
        print("Missing required fields.")
        sys.exit(1)
    if not os.path.isdir(myglue_root):
        print(f"Not a directory: {myglue_root}")
        sys.exit(1)

    preserve_structure = input("Preserve MyGlue folder structure (auto-create matching Zendesk sections)? (Y/n): ").strip().lower() != "n"
    test_mode = input("Test with a single doc first? (y/N): ").strip().lower() == "y"
    single_folder = None
    if test_mode:
        single_folder = input("Full path to the single doc folder (DOC-...): ").strip()
        if not os.path.isdir(single_folder):
            print(f"Not a directory: {single_folder}")
            sys.exit(1)

    csv_data = load_docs_csv(myglue_root)
    print(f"\nLoaded {len(csv_data)} entries from documents.csv")

    auth = (f"{email}/token", api_token)

    section_cache = {}
    category_id = None
    if preserve_structure:
        try:
            root_section = get_section(subdomain, auth, section_id)
            category_id = root_section["category_id"]
            print(f"Root section belongs to category ID {category_id}. Loading existing sections...")
            section_cache = list_category_sections(subdomain, auth, category_id)
            print(f"Loaded {len(section_cache)} existing section(s) in this category.")
        except Exception as e:
            print(f"[ERROR] Could not read section/category info: {e}")
            sys.exit(1)

    if test_mode:
        name = os.path.basename(os.path.abspath(single_folder))
        m = DOC_FOLDER_RE.match(name)
        if not m:
            print(f"Folder name doesn't match DOC-<orgid>-<docid> format: {name}")
            sys.exit(1)
        docs_root = os.path.join(myglue_root, "documents")
        if not os.path.isdir(docs_root):
            docs_root = myglue_root
        try:
            rel = os.path.relpath(os.path.dirname(os.path.abspath(single_folder)), docs_root)
            parents = [] if rel == "." else rel.split(os.sep)
        except Exception:
            parents = []
        docs_to_process = [{
            "doc_id": m.group(1),
            "folder_title": m.group(2),
            "folder_path": single_folder,
            "parents": parents,
        }]
    else:
        docs_to_process = list(find_doc_folders(myglue_root))

    if not docs_to_process:
        print("No documents found.")
        sys.exit(0)

    unique_paths = sorted({normalize_section_name(d["parents"]) for d in docs_to_process if d["parents"]})
    print(f"\nAbout to import {len(docs_to_process)} document(s).")
    if preserve_structure and unique_paths:
        print(f"Will use {len(unique_paths)} section(s) (created if missing):")
        for p in unique_paths:
            marker = "  (existing)" if p.lower() in section_cache else "  (will create)"
            print(f"  - {p}{marker}")
        print(f"Root-level docs go to your given section ID {section_id}.")
    confirm = input("Proceed? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        sys.exit(0)

    stats = {"ok": 0, "fail": 0, "skip": 0}
    for i, doc in enumerate(docs_to_process, start=1):
        print(f"\n[{i}/{len(docs_to_process)}]", end="")
        if preserve_structure:
            try:
                target_section = resolve_section_id(
                    doc["parents"], section_id, section_cache,
                    subdomain, auth, category_id,
                )
            except Exception as e:
                print(f"   [ERROR resolving section] {e}")
                stats["fail"] += 1
                continue
        else:
            target_section = section_id

        result = process_one_doc(
            doc, csv_data, subdomain, auth, target_section,
            base_draft, myglue_root,
        )
        stats[result] += 1

    print(f"\n=== Done. OK: {stats['ok']}, Failed: {stats['fail']}, Skipped: {stats['skip']} ===")


if __name__ == "__main__":
    main()
