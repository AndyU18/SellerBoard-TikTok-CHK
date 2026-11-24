"""Carga los proveedores del Centro de acciones (cada módulo se registra solo al importarse).

Una lista explícita en vez de descubrimiento mágico: se ve de un vistazo qué áreas alimentan el centro.
Un módulo que no se pueda importar se registra en el log y no impide que arranque la aplicación.
"""

from __future__ import annotations

import importlib
import logging

log = logging.getLogger("chk.actions")

PROVIDER_MODULES: tuple[str, ...] = (
    "app.actions_builtin",  # correcciones del carrier, órdenes con pérdida, listados despublicados, creadores sin ventas
    "app.cost_risk",  # SKU con ventas y sin costo cargado
    "app.settlement_aging",  # dinero atorado en liquidaciones
    "app.sample_policy",  # creadores a quienes dejar de enviar muestras
    "app.min_price",  # SKU vendidos por debajo de su precio mínimo con envío real
    "app.walmart_actions",  # pedidos por vencer, calidad de listados, feeds con fallos y repricer de Walmart
)


def load_providers() -> list[str]:
    """Importa los módulos de proveedores y devuelve los que cargaron."""
    loaded: list[str] = []
    for name in PROVIDER_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:  # falta una dependencia DENTRO del módulo: es un error real
                log.exception("Proveedor de acciones %s no cargó", name)
            else:
                log.warning("Proveedor de acciones %s aún no existe", name)
        except Exception:
            log.exception("Proveedor de acciones %s no cargó", name)
        else:
            loaded.append(name)
    return loaded
