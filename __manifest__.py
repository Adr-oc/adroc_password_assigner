# -*- coding: utf-8 -*-
{
    'name': 'Asignador de Contraseñas de Pago',
    'version': '19.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Asigna contraseñas de pago a facturas desde documentos usando IA',
    'description': """
Asignador de Contraseñas de Pago
================================

Este módulo permite asignar contraseñas de pago a facturas (account.move)
a partir de documentos como imágenes, PDFs o archivos Excel.

Características:
- Procesamiento de imágenes y PDFs con OpenAI Vision
- Parsing de archivos Excel con plantillas configurables
- Match parcial de números de factura
- Preview editable antes de aplicar cambios
- Soporte multi-página (IA detecta si son varias contraseñas o una sola)
- Integración como acción en vista lista de facturas

Flujo de trabajo:
1. Subir documentos (imágenes, PDFs, Excel)
2. IA extrae contraseñas y números de factura
3. Sistema busca facturas que coincidan
4. Usuario revisa y edita el preview
5. Aplicar cambios al campo document_password
    """,
    'author': 'Adrian Orantes',
    'website': 'https://portfolio.adrocgt.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'account',
        'mail',
        'mrdc_shipment_base',
    ],
    'external_dependencies': {
        'python': ['pandas', 'openpyxl', 'requests'],
    },
    'data': [
        'security/ir.model.access.csv',
        'views/password_assigner_config_views.xml',
        'views/password_assigner_template_views.xml',
        'views/password_assigner_wizard_views.xml',
        'views/account_move_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            '/adroc_password_assigner/static/src/js/password_assigner_button.js',
            '/adroc_password_assigner/static/src/xml/password_assigner_button.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
