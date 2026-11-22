# myGlue → Zendesk Guide Importer

Moves knowledge-base documents from a **myGlue (IT Glue) export** into **Zendesk Guide** articles, including their images and attachments.
myGlue accounts have no API, so this works from the export download instead.

## What it does

- Walks the export's `documents/` folder and turns each `DOC-<org>-<id> Title` folder into one Zendesk article.
- **Inline images:** uploads every embedded image to the article and points the `<img>` tags at the new Zendesk URLs.
- **Attachments:** uploads files from `attachments/documents/<id>/` and adds an "Attached Files" list at the end of the article.
- **Folder structure:** can optionally create one Zendesk section per myGlue folder path, for example `Folder - Subfolder`.
- **Titles and archived docs:** reads titles from `documents.csv`, and imports archived documents as **drafts**.
- **Test mode:** lets you import one document first.

## Requirements

- Python 3.8+
- A Zendesk API token, and an account with Guide admin rights
- The ID of an existing Zendesk section, which is used as the root

```bash
python3 -m pip install -r requirements.txt
```

## Usage

```bash
python3 zendesk_import_myglue.py
```

The script asks you for:

| Prompt | Example |
|---|---|
| Zendesk subdomain | `yourcompany` (or the full URL) |
| Email | `agent@example.com` |
| API token | hidden input, never stored |
| Section ID | the root section in Zendesk |
| Path to the myGlue export root | `/path/to/MyGlue` |
| Import all as Draft? | `y` is recommended for the first run |
| Preserve folder structure? | `Y` to create matching sections |
| Test with a single doc? | `y` and then the path to one `DOC-...` folder |

It lists the sections it will use or create, and waits for you to confirm before writing anything.

## Expected export layout

```
MyGlue/
├── documents.csv
├── documents/
│   └── <Folder>/<Subfolder>/DOC-1234567-8901234 Article Title/
│       ├── Article Title.html
│       └── images/...
└── attachments/documents/8901234/<files>
```

## Before you run it

- **Visibility:** articles take the default visibility of the section you import into, so import into a section restricted to agents or signed-in users.
  Internal procedures shouldn't end up on a public help center.
- **Only documents are imported.** The export's other CSVs (contacts, configurations and so on) contain personal and device data. The script ignores them, and they shouldn't be committed anywhere.
- **Links between documents** that point back to the old IT Glue URLs aren't rewritten.
- **Start small:** run test mode on one document and check it in Zendesk before running the full import.

## Security

There are no credentials in this script. Your API token is typed in when you run it and stays in memory. Never commit your export folder or a token to this repo.
