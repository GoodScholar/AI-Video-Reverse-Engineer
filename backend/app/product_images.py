"""Public product-image discovery and bounded downloads with pinned public DNS."""
from __future__ import annotations

import http.client
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

IMAGE_LIMIT = 8_000_000
HTML_LIMIT = 1_500_000


def public_target(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('仅支持公开 HTTP 图片地址')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if port not in (80, 443):
        raise ValueError('不支持此网络端口')
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    # Some local network proxies return RFC 2544 synthetic addresses. Resolve the
    # hostname over verified HTTPS, then pin a public result instead of trusting them.
    if ips and any(ip.version == 4 and ip in ipaddress.ip_network('198.18.0.0/15') for ip in ips) and not any(ip.is_global for ip in ips):
        reply = httpx.get('https://cloudflare-dns.com/dns-query', params={'name': parsed.hostname, 'type': 'A'},
                          headers={'accept': 'application/dns-json'}, timeout=8, trust_env=False)
        reply.raise_for_status()
        answer = reply.json()
        ips = [ipaddress.ip_address(item['data']) for item in answer.get('Answer', []) if item.get('type') == 1] if answer.get('Status') == 0 else []
    if not ips or any(not ip.is_global for ip in ips):
        raise ValueError('不允许访问本地或内网地址')
    return parsed, port, str(ips[0])


def safe_fetch(url, limit):
    for _ in range(4):
        parsed, port, address = public_target(url)
        cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        connection = cls(parsed.hostname, port, timeout=8)
        # Pin the validated address; HTTPS still verifies TLS against the original hostname.
        connection._create_connection = lambda target, timeout, *args: socket.create_connection((address, port), timeout)
        try:
            connection.request('GET', (parsed.path or '/') + ('?' + parsed.query if parsed.query else ''), headers={
                'User-Agent': 'AivreProductMedia/1.0', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise ValueError('图片重定向地址无效')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise OSError('图片来源暂时无法读取')
            if int(response.getheader('Content-Length') or 0) > limit:
                raise ValueError('图片或页面超过大小限制')
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError('图片或页面超过大小限制')
            return data, response.getheader('Content-Type') or '', url
        finally:
            connection.close()
    raise ValueError('图片重定向次数过多')


def search_products(name):
    with httpx.Client(timeout=25, trust_env=False) as client:
        response = client.post('https://api.anysearch.com/v1/search', json={'query': name + ' 商品 图片 官方', 'max_results': 6})
        response.raise_for_status()
        result = response.json()
    if result.get('code') != 0:
        raise OSError('商品图片检索暂时不可用')
    return result.get('data', {}).get('results', [])[:6]


class ProductImages(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.product_links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'a' and urlsplit(values.get('href', '')).path.rstrip('/') in ('/product.html', '/products', '/product'):
            self.product_links.append(values['href'])
        if tag == 'meta' and values.get('property') == 'og:image':
            self.images.append((values.get('content', ''), '商品展示'))
        if tag != 'img':
            return
        name = values.get('alt', '').strip()
        source = values.get('data-src') or values.get('src') or ''
        if any(word in (name + ' ' + source).lower() for word in ('logo', 'icon', 'avatar', '二维码', '关于', '新闻中心', '有限责任公司', '人才招聘', '大窑品牌', '联系我们', 'qrcode', 'wechat', 'weixin', 'zxcode_', '/ewm.', '二维码')):
            return
        for dimension in ('width', 'height'):
            if values.get(dimension, '').isdigit() and int(values[dimension]) < 120:
                return
        self.images.append((source, name or '商品展示'))


def discover_images(results, fetch=safe_fetch, product_name=""):
    def collect(result):
        try:
            data, content_type, page = fetch(result['url'], HTML_LIMIT)
            if 'html' not in content_type:
                return []
            parser = ProductImages()
            parser.feed(data.decode('utf-8', errors='replace'))
            for link in parser.product_links[:1]:
                product_page = urljoin(page, link)
                if product_page != page and urlsplit(product_page).hostname == urlsplit(page).hostname:
                    try:
                        product_data, product_type, final_page = fetch(product_page, HTML_LIMIT)
                        if 'html' in product_type:
                            products = ProductImages()
                            products.feed(product_data.decode('utf-8', errors='replace'))
                            if products.images:
                                parser, page = products, final_page
                    except (OSError, ValueError, http.client.HTTPException, httpx.HTTPError):
                        pass
            return [{'title': title[:200], 'pageUrl': page, 'imageUrl': urljoin(page, source)}
                    for source, title in parser.images if source and urlsplit(urljoin(page, source)).scheme in ('http', 'https')][:12]
        except (OSError, ValueError, KeyError, http.client.HTTPException, httpx.HTTPError):
            return []
    with ThreadPoolExecutor(max_workers=3) as pool:
        groups = list(pool.map(collect, results[:6]))
    # Balance sources so one site's banners or blocked CDN cannot occupy every slot.
    if product_name:
        query_chars = set(product_name)
        for group in groups:
            group.sort(key=lambda item: len(query_chars & set(item['title'])), reverse=True)
    unique = {}
    for index in range(12):
        for group in groups:
            if index < len(group):
                candidate = group[index]
                unique.setdefault(candidate['imageUrl'], candidate)
    return list(unique.values())[:8]
