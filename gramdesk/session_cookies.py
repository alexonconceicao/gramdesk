"""Detecção de login pelo cookie "sessionid" do Instagram, sem depender do Qt (testável)."""

SessionKey = tuple[str, str, bytes]


def track_session(sessions: set[SessionKey], name: bytes, domain: str, path: str, value: bytes, added: bool) -> bool:
    """Atualiza o conjunto de sessões presentes e diz se a conta está logada.

    O Instagram rotaciona o sessionid adicionando o novo antes de remover o antigo; por isso cada
    cookie é identificado também pelo valor, e a remoção do antigo não derruba o login.
    """
    if name == b"sessionid" and domain.lstrip(".").endswith("instagram.com"):
        key = (domain.lstrip("."), path, value)
        if added and value:
            sessions.add(key)
        else:
            sessions.discard(key)
    return bool(sessions)
