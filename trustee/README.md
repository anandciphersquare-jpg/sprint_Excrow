# Trustee suite (placeholder)

Reserved for the Trustee portal, mirroring the `admin/` and `partner/`
layout:

```
trustee/
├── conftest.py     # trustee_context / trustee_page / logged_in_trustee_page
├── pages/          # login_page.py, ...
├── tests/          # test_login.py, ...
└── data/
```

When the Trustee portal is ready:
1. Add `TRUSTEE_BASE_URL` / credentials to `config/settings.py` + `.env.example`.
2. Copy `partner/conftest.py` as a starting point and rename fixtures.
3. Add page objects and tests; tag them with the `trustee` marker.
