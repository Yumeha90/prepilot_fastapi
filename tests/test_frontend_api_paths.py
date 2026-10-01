"""守卫：前端 api/ 下的请求路径必须带 /api 前缀。

背景：`client.ts` 的 baseURL 故意留空（本地靠 vite 代理 /api → 8010，云端靠 nginx 反代），
所以每个 api/*.ts 里的路径都得写全 `/api/...`。漏写时请求会打在静态资源上：
本地 vite 返回 404、云端 nginx 返回 405，而页面只会报一个含糊的"请求失败"，很难定位。
（2026-10-01 修：resume.ts 与 candidates.ts 都漏过。）

只做静态扫描，不启浏览器，成本可忽略。
"""

import re
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1] / "frontend" / "src" / "api"

# client.ts 自己定义 baseURL 且显式带 /api，跳过
_SKIP = {"client.ts"}

# apiClient.get|post|... 之后紧跟的字面量（允许跨行，故用 DOTALL）
_CALL_RE = re.compile(
    r"""(?:apiClient|axios)\.(?:get|post|put|patch|delete|request)\s*(?:<[^>]*>)?\(\s*['"`]([^'"`]+)['"`]""",
    re.DOTALL,
)
# const BASE = '/xxx' —— 模板串 `${BASE}/${id}` 靠这一条兜住
_BASE_RE = re.compile(r"""const\s+BASE\s*=\s*['"`]([^'"`]+)['"`]""")


def _collect() -> list[tuple[str, int, str]]:
    """返回 [(文件名, 行号, 路径)]，模板串里的 ${BASE} 已按同文件的 BASE 常量展开。"""
    out: list[tuple[str, int, str]] = []
    for f in sorted(API_DIR.glob("*.ts")):
        if f.name in _SKIP:
            continue
        text = f.read_text(encoding="utf-8")
        base_m = _BASE_RE.search(text)
        base = base_m.group(1) if base_m else None

        for m in _CALL_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            path = m.group(1)
            if "${BASE}" in path:
                # 没有 BASE 可展开时原样保留，让断言报错暴露出来
                path = path.replace("${BASE}", base) if base else path
            out.append((f.name, line, path))

        if base_m:
            line = text.count("\n", 0, base_m.start()) + 1
            out.append((f.name, line, base))
    return out


@pytest.mark.skipif(not API_DIR.is_dir(), reason="frontend/src/api 不存在")
def test_all_api_paths_have_api_prefix():
    bad = [(f, i, p) for f, i, p in _collect() if not p.startswith("/api/")]
    assert not bad, "以下请求缺少 /api 前缀（会 404/405）：\n" + "\n".join(
        f"  {f}:{i}  {p}" for f, i, p in bad
    )


@pytest.mark.skipif(not API_DIR.is_dir(), reason="frontend/src/api 不存在")
def test_guard_itself_finds_paths():
    """自检：扫描器确实抓到了路径，否则上面那条断言等于没跑。"""
    found = _collect()
    assert len(found) >= 5, f"只扫到 {len(found)} 条，正则可能失效了"
