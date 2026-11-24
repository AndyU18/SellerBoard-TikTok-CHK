// Guía en español de los eventos de webhook documentados por TikTok Shop (mismos nombres que
// DOCUMENTED_WEBHOOK_EVENTS del backend): qué avisa cada uno y si conviene suscribirlo.
export type EventGroup = 'Órdenes' | 'Devoluciones' | 'Paquetes' | 'Productos' | 'Cuenta'

export interface EventGuide {
  title: string
  group: EventGroup
  recommended: boolean
  why: string
}

export const EVENT_GUIDE: Record<string, EventGuide> = {
  ORDER_STATUS_CHANGE: {
    title: 'Cambio de estado de una orden',
    group: 'Órdenes',
    recommended: true,
    why: 'Avisa al instante de ventas nuevas, pagos y envíos; evita esperar la consulta de cada 6 horas.',
  },
  CANCELLATION_STATUS_CHANGE: {
    title: 'Cancelación de una orden',
    group: 'Órdenes',
    recommended: true,
    why: 'Permite detener el envío a tiempo y no contar como venta una orden cancelada.',
  },
  RECIPIENT_ADDRESS_UPDATE: {
    title: 'Cambio de dirección del comprador',
    group: 'Órdenes',
    recommended: false,
    why: 'Útil para corregir el envío antes de despachar; en esta app no se guardan direcciones.',
  },
  RETURN_STATUS_CHANGE: {
    title: 'Cambio de estado de una devolución',
    group: 'Devoluciones',
    recommended: true,
    why: 'Detecta devoluciones y reembolsos apenas se abren, que es lo que más afecta la utilidad.',
  },
  REVERSE_ORDER_STATUS_UPDATE: {
    title: 'Cambio en una orden inversa',
    group: 'Devoluciones',
    recommended: true,
    why: 'Sigue el trayecto de reembolsos y devoluciones hasta que se cierran.',
  },
  PACKAGE_UPDATE: {
    title: 'Cambio en un paquete',
    group: 'Paquetes',
    recommended: true,
    why: 'Muestra paquetes que se atascan o se entregan sin esperar a la siguiente sincronización.',
  },
  PRODUCT_STATUS_CHANGE: {
    title: 'Cambio de estado de un producto',
    group: 'Productos',
    recommended: true,
    why: 'Avisa cuando TikTok rechaza o suspende un producto y deja de vender.',
  },
  INVENTORY_UPDATE: {
    title: 'Cambio de inventario',
    group: 'Productos',
    recommended: true,
    why: 'Alerta de existencias que bajan o se agotan para reponer a tiempo.',
  },
  PRODUCT_INFORMATION_CHANGE: {
    title: 'Cambio de información de un producto',
    group: 'Productos',
    recommended: false,
    why: 'Informativo: ediciones de título, precio o imágenes hechas en TikTok.',
  },
  SELLER_DEAUTHORIZATION: {
    title: 'Se retiró la autorización de la app',
    group: 'Cuenta',
    recommended: false,
    why: 'Avisa si la conexión se corta; hoy se detecta al fallar la sincronización.',
  },
  UPCOMING_AUTHORIZATION_EXPIRATION: {
    title: 'La autorización está por vencer',
    group: 'Cuenta',
    recommended: false,
    why: 'Recordatorio previo para renovar la conexión antes de perder datos.',
  },
  NEW_MESSAGE: {
    title: 'Mensaje nuevo de un comprador',
    group: 'Cuenta',
    recommended: false,
    why: 'Solo sirve si se atiende el chat; esta app no lee mensajes.',
  },
}

export const guideFor = (eventType: string): EventGuide =>
  EVENT_GUIDE[eventType] ?? { title: eventType, group: 'Cuenta', recommended: false, why: 'Evento documentado por TikTok Shop sin descripción local.' }
