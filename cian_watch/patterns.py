import re

SERP_CONFIG_RE = re.compile(
    r"window\._cianConfig\['frontend-serp'\]\s*=\s*"
    r"\(window\._cianConfig\['frontend-serp'\]\s*\|\|\s*\[\]\)\.concat\("
)
