"""Spanish for the parts of the demo page that come from recordings.

Recorded data (model answers, alert summaries, fault descriptions) is in English. The page keeps
the original, because that is what really happened, and adds a Spanish version when one is known:
hand translations of the recorded texts, and simple patterns for alert summaries. Anything not
known here is shown only in the original. Every value is escaped by the caller's helpers.
"""

import re
from html import escape

# what each recorded diagnosis said (eval/cases/chaos-*.json, live.summary)
SUMMARY_ES = {
    "The catalog service is down, causing orders to be slow and show errors.": "El servicio de catálogo está caído, y por eso pedidos va lento y muestra errores.",
    "The catalog service's new rollout is likely causing high latency and affecting orders.": "Probablemente la nueva versión del catálogo causa la latencia alta y afecta a pedidos.",
    "The orders service is down because it has been scaled to 0 replicas.": "El servicio de pedidos está caído porque lo escalaron a 0 réplicas.",
    "The new rollout of the orders service likely caused a memory leak, leading to high memory usage and startup probe failures.": "Probablemente la nueva versión de pedidos causó una fuga de memoria: uso alto de memoria y fallos en la sonda de arranque.",
    "Payments service is down, causing upstream errors and high error rates in orders and payments.": "El servicio de pagos está caído, y provoca errores en las llamadas y muchos errores en pedidos y pagos.",
    "Payments is down because it was scaled to 0 replicas, causing upstream errors in orders.": "Pagos está caído porque lo escalaron a 0 réplicas, y eso provoca errores en pedidos.",
    "PostgreSQL is scaled to zero replicas, causing `catalog`, `orders`, and `payments` to fail connections and return 5xx errors.": "PostgreSQL está escalado a cero réplicas, y por eso catalog, orders y payments no logran conectarse y devuelven errores 5xx.",
    "Redis is scaled to zero replicas, causing the catalog cache to fail and increasing database load.": "Redis está escalado a cero réplicas: falla la caché del catálogo y sube la carga de la base de datos.",
}

# how each fault was caused (eval/cases/chaos-*.json, fault)
FAULT_ES = {
    "new catalog release adds 1.5 s to every request (fix: rollback)": "nueva versión de catalog que añade 1,5 s a cada petición (arreglo: volver a la anterior)",
    "new orders release leaks 64 KiB per request (fix: rollback; a restart buys time)": "nueva versión de orders que pierde 64 KiB de memoria por petición (arreglo: volver a la anterior; un reinicio da tiempo)",
    "new payments release fails 50% of requests (fix: rollback)": "nueva versión de payments en la que falla el 50 % de las peticiones (arreglo: volver a la anterior)",
    "PostgreSQL StatefulSet scaled to 0 (never touched by the agent)": "StatefulSet de PostgreSQL escalado a 0 (el agente nunca lo toca)",
}
_SCALED = re.compile(r"^(\w+) Deployment scaled to 0 replicas$")

SERVICE_ES = {
    "catalog": "el catálogo",
    "orders": "pedidos",
    "payments": "pagos",
    "redis": "la caché",
    "postgres": "la base de datos",
}
_ALERTS = (
    (re.compile(r"^(\w+) is down$"), "{0} no responde"),
    (re.compile(r"^(\w+) is slow$"), "{0} va lento"),
    (re.compile(r"^(\w+) cannot reach (\w+)$"), "{0} no puede comunicarse con {1}"),
    (re.compile(r"^(\w+) returns too many 5xx errors$"), "{0} devuelve demasiados errores 5xx"),
    (re.compile(r"^(\w+) uses too much memory$"), "{0} usa demasiada memoria"),
    (
        re.compile(r"^catalog cache \(Redis\) is failing$"),
        "la caché del catálogo (Redis) está fallando",
    ),
)

# labels of the architecture diagram
ARCH_ES = {
    "shop namespace": "namespace de la tienda",
    "observability": "observabilidad",
    "centinela namespace": "namespace de Centinela",
    "outside the cluster": "fuera del clúster",
    "alerts, metrics": "alarmas, métricas",
    "logs": "registros",
    "collector": "colector",
    "alerts to incidents": "alarmas a incidentes",
    "agent": "agente",
    "read-only account": "cuenta de solo lectura",
    "executor": "ejecutor",
    "scale, restart: 4 deployments": "escala o reinicia: 4 servicios",
    "local LLM": "modelo local",
    "Ollama, own GPU": "Ollama, GPU propia",
    "engineer": "ingeniero",
    "console or CLI": "consola o terminal",
    "metrics": "métricas",
    "evidence": "pruebas",
    "proposal": "propuesta",
    "approves": "aprueba",
    "scales or restarts, then verifies": "escala o reinicia, y verifica",
}


def summary_es(text: str) -> str | None:
    return SUMMARY_ES.get(text)


def fault_es(text: str) -> str | None:
    if text in FAULT_ES:
        return FAULT_ES[text]
    m = _SCALED.match(text)
    return f"Deployment de {m.group(1)} escalado a 0 réplicas" if m else None


def alert_es(text: str) -> str | None:
    for pattern, template in _ALERTS:
        m = pattern.match(text)
        if m and all(g in SERVICE_ES for g in m.groups()):
            return template.format(*(SERVICE_ES[g] for g in m.groups()))
    return None


def bilingual(original: str, spanish: str | None) -> str:
    """Escaped HTML: the Spanish version for Spanish readers, the original for everyone else."""
    if spanish is None:
        return escape(original)
    return f'<span class="es">{escape(spanish)}</span><span class="en">{escape(original)}</span>'


_TEXT = re.compile(r'<text class="([^"]+)"([^>]*)>([^<]+)</text>')


def bilingual_svg(svg: str) -> str:
    """Each known <text> label becomes two: one shown in Spanish, one in English."""

    def swap(m: re.Match[str]) -> str:
        cls, attrs, label = m.groups()
        es = ARCH_ES.get(label)
        if es is None:
            return m.group(0)
        return (
            f'<text class="{cls} es"{attrs}>{escape(es)}</text>'
            f'<text class="{cls} en"{attrs}>{label}</text>'
        )

    return _TEXT.sub(swap, svg)
