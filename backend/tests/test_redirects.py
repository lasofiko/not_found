"""Legacy URLs must never redirect a browser to another origin."""

from urllib.parse import urljoin, urlsplit

import pytest

from backend.redirects import legacy_dashboard_location


@pytest.mark.parametrize(
    "path",
    [
        "/example.com",
        "%2Fexample.com",
        "%252fexample.com",
        "\\example.com",
        "%5Cexample.com",
        "/\\example.com",
        "%2f%5cexample.com",
        "%zz",
        "%FF",
        "\nexample.com",
        "../example.com",
    ],
)
def test_helper_rejects_suspicious_paths(path):
    assert legacy_dashboard_location(path) == "/"


@pytest.mark.parametrize(
    "path, expected",
    [
        ("", "/"),
        ("index.html", "/"),
        ("capacity", "/#capacity"),
        ("capacity/", "/#capacity"),
        ("capacity.html", "/#capacity"),
        ("assets/app.js", "/assets/app.js"),
        ("forecast/2030", "/forecast/2030"),
        ("hello%20world", "/hello%20world"),
    ],
)
def test_ordinary_links_keep_internal_destination(path, expected):
    assert legacy_dashboard_location(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard//example.com",
        "/dashboard/%2Fexample.com",
        "/dashboard/%252Fexample.com",
        "/dashboard/%5Cexample.com",
        "/dashboard/%2F%5Cexample.com",
        "/dashboard/%zz",
    ],
)
def test_fastapi_redirect_stays_on_same_origin(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert location == "/"
    assert urlsplit(urljoin("https://trusted.example", location)).netloc == "trusted.example"
