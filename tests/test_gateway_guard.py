from oncf.http import is_gateway_resource_url

BASE = "https://shortora.duckdns.org/oncf"


def test_allows_gateway_resources() -> None:
    assert is_gateway_resource_url(BASE, f"{BASE}/media/static/fr/images/a.pdf")
    assert is_gateway_resource_url(BASE, f"{BASE}/media/CMS/fr/pages")
    assert is_gateway_resource_url(BASE, f"{BASE}/grids.json")


def test_rejects_upstream_and_plain_http() -> None:
    assert not is_gateway_resource_url(
        BASE, "https://upstream.example/media/static/fr/images/a.pdf"
    )
    assert not is_gateway_resource_url(
        BASE, "https://mobile.example/media/static/fr/images/a.pdf"
    )
    assert not is_gateway_resource_url(BASE, f"http://shortora.duckdns.org/oncf/grids.json")


def test_rejects_path_escape_and_other_hosts() -> None:
    assert not is_gateway_resource_url(BASE, f"{BASE}/../config")
    assert not is_gateway_resource_url(BASE, "https://shortora.duckdns.org/config")
    assert not is_gateway_resource_url(BASE, "https://shortora.duckdns.org.evil.example/oncf/x")
    assert not is_gateway_resource_url(BASE, "https://example.com/oncf/x")
