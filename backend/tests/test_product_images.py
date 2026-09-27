import socket
from unittest.mock import patch

import pytest

from app.product_images import public_target, safe_fetch

@pytest.mark.parametrize('url', ['file:///tmp/x', 'http://user:pass@example.com/a', 'http://example.com:8000/a', 'http://127.0.0.1/a', 'http://[::1]/a'])
def test_rejects_nonpublic_urls(url):
    with pytest.raises((ValueError, OSError)):
        public_target(url)


def test_mixed_dns_answers_rejected():
    with patch('app.product_images.socket.getaddrinfo', return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',80)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.2',80))]):
        with pytest.raises(ValueError):
            public_target('http://example.com/a')


def test_redirect_to_private_address_is_revalidated():
    class Reply:
        status=302
        def getheader(self,key): return 'http://127.0.0.1/private' if key=='Location' else None
    class Connection:
        def __init__(self,*args,**kwargs):pass
        def request(self,*args,**kwargs):pass
        def getresponse(self):return Reply()
        def close(self):pass
    real=public_target
    with patch('app.product_images.public_target',side_effect=lambda url: ( __import__('urllib.parse',fromlist=['urlsplit']).urlsplit(url),80,'8.8.8.8') if 'example.com' in url else real(url)), patch('app.product_images.http.client.HTTPConnection',Connection):
        with pytest.raises(ValueError):safe_fetch('http://example.com/a',100)


def test_proxy_synthetic_dns_uses_verified_public_dns():
    from app.product_images import public_target
    class Reply:
        def raise_for_status(self):pass
        def json(self):return {'Status':0,'Answer':[{'type':1,'data':'8.8.8.8'}]}
    with patch('app.product_images.socket.getaddrinfo', return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.0.4',443))]), patch('app.product_images.httpx.get',return_value=Reply()):
        assert public_target('https://brand.example/a')[2]=='8.8.8.8'


def test_discovery_prefers_same_site_product_page_and_ignores_bad_sources():
    import httpx
    from app.product_images import discover_images
    def fetch(url,limit):
        if 'bad.example' in url:raise httpx.ConnectTimeout('timeout')
        if url.endswith('product.html'):
            return b'<img src="/bottle.png" alt="Product bottle">','text/html',url
        return b'<a href="/product.html">products</a><img src="/wechat.png" alt="qrcode">','text/html',url
    result=discover_images([{'url':'https://brand.example/news.html'},{'url':'https://bad.example'}],fetch)
    assert len(result)==1
    assert result[0]['pageUrl']=='https://brand.example/product.html'
    assert result[0]['imageUrl']=='https://brand.example/bottle.png'


def test_download_enforces_body_limit_and_pins_validated_address():
    from urllib.parse import urlsplit
    class Reply:
        status=200
        def getheader(self,key):return 'image/png' if key=='Content-Type' else None
        def read(self,limit):return b'x' * limit
    connections=[]
    class Connection:
        def __init__(self,*args,**kwargs):connections.append(self)
        def request(self,*args,**kwargs):self._create_connection(('brand.example',443),8)
        def getresponse(self):return Reply()
        def close(self):pass
    with patch('app.product_images.public_target',return_value=(urlsplit('https://brand.example/a'),443,'8.8.8.8')),patch('app.product_images.http.client.HTTPSConnection',Connection),patch('app.product_images.socket.create_connection') as connect:
        with pytest.raises(ValueError):safe_fetch('https://brand.example/a',10)
        connect.assert_called_once_with(('8.8.8.8',443),8)


def test_image_validation_rejects_small_icons_and_gif():
    import io
    from PIL import Image
    from app.product_images_api import image_info
    for size,fmt in (((40,40),'PNG'),((400,600),'GIF')):
        data=io.BytesIO();Image.new('RGB',size).save(data,format=fmt)
        with pytest.raises(ValueError):image_info(data.getvalue())
