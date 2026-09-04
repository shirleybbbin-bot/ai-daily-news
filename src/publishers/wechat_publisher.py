"""WeChat Official Account publisher — creates draft via API.

Image handling pipeline:
1. Find all <img src="..."> in HTML content
2. Download each external image locally
3. Upload to WeChat via uploadimg API → get WeChat URL
4. Replace src in HTML with WeChat URL
5. Use first image (or designated cover) as thumb_media_id
"""
import os
import re
import time
import json
import requests
from typing import Optional, Dict, List
from urllib.parse import urljoin


def get_access_token(app_id: str, app_secret: str) -> Optional[str]:
    """Get WeChat access token."""
    url = "https://api.weixin.qq.com/cgi-bin/token"
    params = {
        'grant_type': 'client_credential',
        'appid': app_id,
        'secret': app_secret,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if 'access_token' in data:
            print("  [WeChat] Got access token")
            return data['access_token']
        else:
            print(f"  [WeChat] Error getting token: {data}")
            return None
    except Exception as e:
        print(f"  [WeChat] Token request failed: {e}")
        return None


def upload_content_image(access_token: str, image_path: str) -> Optional[str]:
    """Upload image for use inside article content. Returns WeChat URL.

    Uses: cgi-bin/media/uploadimg — returns a URL (not media_id)
    This URL can be used in <img src="..."> within article HTML.
    """
    url = f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={access_token}"
    try:
        with open(image_path, 'rb') as f:
            resp = requests.post(url, files={'media': f}, timeout=30)
            data = resp.json()
            if 'url' in data:
                return data['url']
            print(f"  [WeChat] Content image upload error: {data}")
            return None
    except Exception as e:
        print(f"  [WeChat] Content image upload failed: {e}")
        return None


def upload_thumb_image(access_token: str, image_path: str) -> Optional[str]:
    """Upload image as permanent media (for cover/thumb). Returns media_id.

    Uses: cgi-bin/material/add_material — returns media_id
    """
    url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={access_token}&type=image"
    try:
        with open(image_path, 'rb') as f:
            resp = requests.post(url, files={'media': f}, timeout=30)
            data = resp.json()
            if 'media_id' in data:
                return data['media_id']
            print(f"  [WeChat] Thumb upload error: {data}")
            return None
    except Exception as e:
        print(f"  [WeChat] Thumb upload failed: {e}")
        return None


def download_image(url: str, save_path: str = None) -> Optional[str]:
    """Download an image from URL to local file."""
    if save_path is None:
        save_path = f"/tmp/wechat_img_{hash(url) % 100000}.jpg"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": url,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15, stream=True)
        resp.raise_for_status()

        content_type = resp.headers.get('Content-Type', '')
        if 'image' not in content_type and 'octet-stream' not in content_type:
            return None

        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        if os.path.getsize(save_path) < 2000:  # Skip tiny images
            os.remove(save_path)
            return None

        return save_path
    except Exception as e:
        return None


def migrate_images_for_wechat(html: str, access_token: str) -> tuple:
    """Find all external images in HTML, upload to WeChat, replace URLs.

    Returns: (updated_html, thumb_media_id)
    """
    # Find all img src URLs
    img_pattern = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
    image_urls = img_pattern.findall(html)

    if not image_urls:
        return html, ''

    print(f"  [WeChat] Found {len(image_urls)} images to migrate")

    # WeChat has rate limits: ~10 images per minute for uploadimg
    # We'll process up to 30 images
    thumb_media_id = ''
    replacements = {}

    for i, img_url in enumerate(image_urls[:30]):
        if img_url.startswith('https://mmbiz.qpic.cn'):
            # Already a WeChat URL, skip
            continue

        print(f"    [{i+1}/{min(len(image_urls),30)}] Downloading: {img_url[:50]}...")

        local_path = download_image(img_url)
        if not local_path:
            print(f"      ⚠️ Download failed, skipping")
            continue

        # Upload as content image (returns URL)
        wechat_url = upload_content_image(access_token, local_path)
        if wechat_url:
            replacements[img_url] = wechat_url
            print(f"      ✅ Uploaded → {wechat_url[:50]}...")

            # Use first image as thumbnail (cover)
            if not thumb_media_id:
                thumb_id = upload_thumb_image(access_token, local_path)
                if thumb_id:
                    thumb_media_id = thumb_id
                    print(f"      ✅ Set as cover image")

        # Clean up temp file
        try:
            os.remove(local_path)
        except OSError:
            pass

        # Rate limit: wait between uploads
        time.sleep(1)

    # Replace all URLs in HTML
    for old_url, new_url in replacements.items():
        html = html.replace(old_url, new_url)

    not_migrated = len(image_urls) - len(replacements)
    if not_migrated > 0:
        print(f"  [WeChat] {not_migrated} images could not be migrated (removed)")
        # Remove unmigrated img tags
        for img_url in image_urls:
            if img_url not in replacements:
                # Remove the entire img tag if image couldn't be migrated
                html = re.sub(
                    r'<img[^>]+src=["\']' + re.escape(img_url) + r'["\'][^>]*/?>',
                    '',
                    html,
                    flags=re.IGNORECASE
                )

    print(f"  [WeChat] Image migration complete: {len(replacements)}/{len(image_urls)} migrated")
    return html, thumb_media_id


def create_draft(access_token: str, article: Dict) -> Optional[str]:
    """Create a draft article in WeChat Official Account."""
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={access_token}"

    payload = {
        "articles": [
            {
                "title": article.get('title', ''),
                "author": article.get('author', 'AI洞察局'),
                "digest": article.get('digest', ''),
                "content": article.get('content', ''),
                "thumb_media_id": article.get('thumb_media_id', ''),
                "content_source_url": article.get('content_source_url', ''),
                "need_open_comment": article.get('need_open_comment', 0),
                "only_fans_can_comment": article.get('only_fans_can_comment', 0),
            }
        ]
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)
        data = resp.json()
        if 'media_id' in data:
            print(f"  [WeChat] Draft created: {data['media_id']}")
            return data['media_id']
        else:
            print(f"  [WeChat] Draft creation error: {data}")
            return None
    except Exception as e:
        print(f"  [WeChat] Draft creation failed: {e}")
        return None


def publish_to_wechat(html_content: str, title: str, digest: str,
                      cover_image_url: str = None) -> bool:
    """Full pipeline: get token → migrate images → create draft.

    Steps:
    1. Get access token
    2. Find all <img> in HTML, download + upload to WeChat, replace URLs
    3. Use first image (or cover_image_url) as thumbnail
    4. Create draft article
    """
    app_id = os.environ.get('WECHAT_APP_ID', '')
    app_secret = os.environ.get('WECHAT_APP_SECRET', '')

    if not app_id or not app_secret:
        print("  [WeChat] Missing APP_ID or APP_SECRET, skipping publish")
        return False

    token = get_access_token(app_id, app_secret)
    if not token:
        return False

    # If a specific cover image URL is provided, add it as the first image
    if cover_image_url:
        cover_tag = f'<img src="{cover_image_url}" style="width:100%;" />'
        html_content = cover_tag + html_content

    # Migrate all external images to WeChat
    html_content, thumb_media_id = migrate_images_for_wechat(html_content, token)

    # If no thumb_media_id from images, try to use the cover_image_url directly
    if not thumb_media_id and cover_image_url:
        local_path = download_image(cover_image_url)
        if local_path:
            thumb_media_id = upload_thumb_image(token, local_path) or ''
            try:
                os.remove(local_path)
            except OSError:
                pass

    article = {
        'title': title,
        'author': 'AI洞察局',
        'digest': digest,
        'content': html_content,
        'thumb_media_id': thumb_media_id,
        'content_source_url': '',
        'need_open_comment': 0,
        'only_fans_can_comment': 0,
    }

    draft_id = create_draft(token, article)
    return draft_id is not None
