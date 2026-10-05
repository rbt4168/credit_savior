from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import WorkflowError


def public_ntu_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname
            or not parsed.hostname.endswith('.ntu.edu.tw') or parsed.username
            or parsed.password or parsed.port not in (None, 80, 443) or parsed.query):
        raise WorkflowError('external_material_origin_unsupported')


class NTURedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_ntu_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_pdf(url):
    public_ntu_url(url)
    try:
        with build_opener(NTURedirect()).open(Request(url), timeout=30) as response:
            data = response.read(25 * 1024 * 1024 + 1)
        if len(data) > 25 * 1024 * 1024:
            raise WorkflowError('attachment_too_large')
        if not data.startswith(b'%PDF-'):
            raise WorkflowError('external_material_invalid')
        return data
    except WorkflowError:
        raise
    except Exception:
        raise WorkflowError('external_material_download_failed', transient=True) from None
