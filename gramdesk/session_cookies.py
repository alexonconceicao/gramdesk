"""Detecção de login pelos cookies de sessão do Instagram, sem depender do Qt (testável).

"sessionid" é HttpOnly e nem sempre é entregue ao app pelo QtWebEngine; "ds_user_id" só existe com a
conta logada e é removido no logout, então qualquer um dos dois indica login.
"""

LOGIN_COOKIES = (b"sessionid", b"ds_user_id")

SessionKey = tuple[bytes, str, str, bytes]


def track_session(sessions: set[SessionKey], name: bytes, domain: str, path: str, value: bytes, added: bool) -> bool:
    """Atualiza o conjunto de sessões presentes e diz se a conta está logada.

    O Instagram rotaciona o sessionid adicionando o novo antes de remover o antigo; por isso cada
    cookie é identificado também pelo valor, e a remoção do antigo não derruba o login.
    """
    if name in LOGIN_COOKIES and domain.lstrip(".").endswith("instagram.com"):
        key = (name, domain.lstrip("."), path, value)
        if added and value:
            sessions.add(key)
        else:
            sessions.discard(key)
    return bool(sessions)
