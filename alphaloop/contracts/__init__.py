"""对外契约层：alphaloop 与消费方（news、apps、evalharness）之间的共享数据结构。

本层只定义数据结构与协议，不含业务逻辑；news 等消费方仅允许 import
本层、infra 与 llm 客户端，不得触及研究平台内部实现。
"""
