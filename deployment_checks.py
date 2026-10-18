"""Bounded public HTTP checks for a server-owned project URL, not arbitrary URLs."""
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import requests

MAX_RESPONSE = 2 * 1024 * 1024


def origin(url):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid deployment address')
    return p.scheme.lower(),p.hostname.lower(),p.port or (443 if p.scheme=='https' else 80)


class Assets(HTMLParser):
    def __init__(self): super().__init__(); self.paths=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        value=a.get('src') if tag=='script' else a.get('href') if tag=='link' and a.get('rel')=='stylesheet' else None
        if value and len(self.paths)<12:self.paths.append(value)


def _verify(public_url, tunnel, session):
    allowed={origin(public_url)}
    if tunnel:
        p=urlsplit(tunnel)
        import re
        if p.scheme!='https' or not re.fullmatch(r'[a-z0-9-]+\.trycloudflare\.com',p.netloc):
            raise ValueError('Invalid runner tunnel address')
        allowed.add(origin(tunnel))
    session.trust_env=False
    deadline=time.monotonic()+40
    def fetch(url):
        for attempt in range(4):
            if time.monotonic()>deadline:raise ValueError('Public verification exceeded its time budget')
            if origin(url) not in allowed:raise ValueError('Public page redirected outside this deployment')
            with session.get(url,timeout=8,allow_redirects=False,stream=True) as response:
                if response.status_code in (301,302,303,307,308):
                    url=urljoin(url,response.headers.get('Location',''))
                    continue
                if response.status_code!=200:raise ValueError(f'Public HTTP check returned {response.status_code}')
                data=bytearray()
                for chunk in response.iter_content(16384):
                    data.extend(chunk)
                    if len(data)>MAX_RESPONSE:raise ValueError('Public response is too large for a smoke check')
                    if time.monotonic()>deadline:raise ValueError('Public verification exceeded its time budget')
                if not data:raise ValueError('Public response was empty')
                return url,response.headers.get('Content-Type',''),bytes(data)
        raise ValueError('Too many public redirects')
    final,kind,body=fetch(public_url)
    assets=[]
    if 'text/html' in kind:
        parser=Assets();parser.feed(body.decode('utf-8',errors='replace'))
        for path in dict.fromkeys(parser.paths):
            url=urljoin(final,path)
            try:
                if origin(url) not in allowed:continue  # third-party assets are not claimed as checked
            except ValueError:
                continue
            _,asset_kind,_=fetch(url)
            if 'text/html' in asset_kind:
                raise ValueError('A script or stylesheet returned HTML instead of an asset')
            assets.append(urlsplit(url).path)
    return {'public_url':public_url,'http':200,'same_origin_assets':assets,
            'scope':'HTTP smoke check only. Run project tests and browser assertions separately.'}


def verify(public_url, tunnel='', session=None):
    if session is not None:
        return _verify(public_url,tunnel,session)
    with requests.Session() as client:
        return _verify(public_url,tunnel,client)
