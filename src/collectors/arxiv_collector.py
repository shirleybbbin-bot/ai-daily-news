"""arXiv paper collector."""
import re
from typing import List, Dict
from utils.http import fetch_url
from utils.text import clean_text
from xml.etree import ElementTree as ET


def collect_arxiv(config: Dict) -> List[Dict]:
    """Collect recent AI papers from arXiv."""
    categories = config.get('categories', ['cs.AI'])
    max_papers = config.get('max_papers', 10)
    keyword_boost = config.get('keyword_boost', '')
    category_tag = config.get('category_tag', '论文精选')

    print("  [arXiv] Fetching recent AI papers...")

    cat_query = '%20OR%20'.join([f'cat:{c}' for c in categories])
    url = f"http://export.arxiv.org/api/query?search_query=({cat_query})&sortBy=submittedDate&sortOrder=descending&max_results={max_papers * 2}"

    xml_text = fetch_url(url, timeout=30)
    if not xml_text:
        print("    [!] Failed to fetch arXiv")
        return []

    items = []
    try:
        root = ET.fromstring(xml_text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}

        for entry in root.findall('atom:entry', ns):
            title = clean_text(entry.find('atom:title', ns).text if entry.find('atom:title', ns) is not None else '')
            summary = clean_text(entry.find('atom:summary', ns).text if entry.find('atom:summary', ns) is not None else '')
            link_elem = entry.find('atom:id', ns)
            link = link_elem.text if link_elem is not None else ''

            # Atom <published> e.g. 2026-10-04T09:12:34Z
            pub_elem = entry.find('atom:published', ns)
            pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ''

            authors = []
            for author in entry.findall('atom:author', ns):
                name_elem = author.find('atom:name', ns)
                if name_elem is not None:
                    authors.append(name_elem.text)

            # Extract PDF link
            pdf_link = ''
            for l in entry.findall('atom:link', ns):
                if l.get('title') == 'pdf':
                    pdf_link = l.get('href', '')
                    break

            # Get primary category
            primary_cat = ''
            cat_elem = entry.find('{http://arxiv.org/schemas/atom}primary_category')
            if cat_elem is not None:
                primary_cat = cat_elem.get('term', '')

            if not title:
                continue

            items.append({
                'title': title,
                'summary': summary[:500] if summary else '',
                'url': link,
                'pdf_url': pdf_link,
                'source': 'arXiv',
                'source_domain': 'arxiv.org',
                'category_hint': category_tag,
                'lang': 'en',
                'weight': 1.1,
                'pub_date': pub_date,
                'is_paper': True,
                'authors': authors[:5],
                'arxiv_category': primary_cat,
            })

            if len(items) >= max_papers:
                break

    except Exception as e:
        print(f"    [!] Error parsing arXiv: {e}")
        return []

    print(f"    [OK] arXiv: {len(items)} papers")
    return items
