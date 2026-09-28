from pydantic import BaseModel


class HealthResponse(BaseModel):
    """健康检查。

    云端冒烟探测固定校验 {"status":"ok"}（Jenkins / Ansible 依赖它），
    因此这里不要随意增删字段。
    """

    status: str = "ok"
