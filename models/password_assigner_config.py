# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)

DEFAULT_INSTRUCTIONS = """Eres un asistente especializado en extraer información de documentos de contraseña de pago de Guatemala.

OBJETIVO: Extraer el número de contraseña y TODAS las facturas listadas en el documento.

IDENTIFICACIÓN DE LA CONTRASEÑA:
- Busca "No.", "Nº", "Contraseña", "Número" cerca del encabezado
- Ejemplos: "No. DIS - 5994", "Contraseña: 055648", "No. 12345"
- El número puede tener prefijos como DIS-, CAR-, POP-, etc.

EXTRACCIÓN DE FACTURAS:
- Extrae TODAS las filas de la tabla de facturas
- Los números de factura están en la columna "Factura" o similar
- Formatos comunes:
  * Numéricos: 2483374605, 519783176, 1301012124
  * Con prefijo: TK00023243, TF00010377, GTGTAPM250031725
  * Cortos: 0098, 0010, 0611
  * Largos: FP-MEG-202512-0002
- Extrae también el monto de cada factura (columna "Monto Q." o similar)

MULTI-PÁGINA:
- Si hay varias páginas pero UN SOLO número de contraseña, es UNA sola contraseña con muchas facturas
- Combina todas las facturas de todas las páginas bajo esa contraseña
- Solo crea contraseñas separadas si hay DIFERENTES números de contraseña

EMPRESAS COMUNES:
- DISTELSA (Grupo Distelsa)
- CARTOGUA (Carton de Guatemala)
- La Popular
- Carton Box

Responde en formato JSON estructurado según el schema proporcionado."""


class PasswordAssignerConfig(models.Model):
    _name = 'password.assigner.config'
    _description = 'Configuración de Asignador de Contraseñas'
    _order = 'sequence, id'

    name = fields.Char(
        string='Nombre',
        required=True,
        help='Nombre descriptivo de la configuración'
    )
    sequence = fields.Integer(
        string='Secuencia',
        default=10
    )
    active = fields.Boolean(
        string='Activo',
        default=True
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company,
        help='Dejar vacío para todas las compañías'
    )

    # OpenAI Configuration
    openai_api_key = fields.Char(
        string='OpenAI API Key',
        required=True,
        help='Clave de API de OpenAI (sk-...)'
    )
    openai_api_url = fields.Char(
        string='URL de API',
        default='https://api.openai.com/v1/responses',
        required=True,
        help='URL del endpoint de OpenAI'
    )
    openai_model = fields.Selection([
        ('gpt-4o-mini', 'GPT-4o Mini (Económico)'),
        ('gpt-4o', 'GPT-4o (Balanceado)'),
        ('gpt-5-nano', 'GPT-5 Nano (Más rápido)'),
        ('gpt-5-mini', 'GPT-5 Mini (Rápido)'),
        ('gpt-5', 'GPT-5 (Mejor calidad)'),
        ('gpt-5.1', 'GPT-5.1 (Último, Nov 2025)'),
    ],
        string='Modelo',
        default='gpt-4o-mini',
        required=True,
        help='Modelo de OpenAI. GPT-5 es el más avanzado pero más costoso.'
    )
    openai_instructions = fields.Text(
        string='Instrucciones del Sistema',
        default=DEFAULT_INSTRUCTIONS,
        help='Instrucciones generales para el modelo AI'
    )
    timeout = fields.Integer(
        string='Timeout (segundos)',
        default=120,
        help='Tiempo máximo de espera para la respuesta de OpenAI'
    )

    # JSON Schema for Structured Outputs
    json_schema = fields.Text(
        string='JSON Schema',
        compute='_compute_json_schema',
        store=True,
        help='Schema JSON para Structured Outputs de OpenAI'
    )

    @api.depends('name')
    def _compute_json_schema(self):
        """Genera el JSON Schema para extracción de contraseñas"""
        # OpenAI strict mode requiere que TODAS las propiedades estén en required
        # Para campos opcionales usamos type: ["string", "null"]
        schema = {
            "name": "password_extraction",
            "schema": {
                "type": "object",
                "properties": {
                    "passwords": {
                        "type": "array",
                        "description": "Lista de contraseñas encontradas en el documento",
                        "items": {
                            "type": "object",
                            "properties": {
                                "password_number": {
                                    "type": "string",
                                    "description": "Número de la contraseña de pago"
                                },
                                "issuer_name": {
                                    "type": ["string", "null"],
                                    "description": "Nombre de la empresa que emite la contraseña"
                                },
                                "document_date": {
                                    "type": ["string", "null"],
                                    "description": "Fecha del documento (formato ISO si es posible)"
                                },
                                "payment_date": {
                                    "type": ["string", "null"],
                                    "description": "Fecha estimada de pago si se menciona"
                                },
                                "page_numbers": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "description": "Páginas del documento donde aparece esta contraseña"
                                },
                                "invoices": {
                                    "type": "array",
                                    "description": "Facturas listadas bajo esta contraseña",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "invoice_number": {
                                                "type": "string",
                                                "description": "Número de factura (puede ser corto o completo)"
                                            },
                                            "invoice_series": {
                                                "type": ["string", "null"],
                                                "description": "Serie de la factura si está separada"
                                            },
                                            "amount": {
                                                "type": ["number", "null"],
                                                "description": "Monto de la factura"
                                            },
                                            "currency": {
                                                "type": ["string", "null"],
                                                "description": "Moneda (Q, GTQ, USD, etc.)"
                                            },
                                            "date": {
                                                "type": ["string", "null"],
                                                "description": "Fecha de la factura si se muestra"
                                            }
                                        },
                                        "required": ["invoice_number", "invoice_series", "amount", "currency", "date"],
                                        "additionalProperties": False
                                    }
                                }
                            },
                            "required": ["password_number", "issuer_name", "document_date", "payment_date", "page_numbers", "invoices"],
                            "additionalProperties": False
                        }
                    },
                    "document_type": {
                        "type": "string",
                        "enum": ["single_password", "multiple_passwords", "continuation", "unknown"],
                        "description": "Tipo de documento detectado"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confianza general de la extracción (0-100)"
                    }
                },
                "required": ["passwords", "document_type", "confidence"],
                "additionalProperties": False
            },
            "strict": True
        }
        import json
        for record in self:
            record.json_schema = json.dumps(schema, indent=2, ensure_ascii=False)

    @api.constrains('openai_api_key')
    def _check_api_key(self):
        for record in self:
            if record.openai_api_key and not record.openai_api_key.startswith('sk-'):
                raise ValidationError(_('La API Key de OpenAI debe comenzar con "sk-"'))

    @api.constrains('timeout')
    def _check_timeout(self):
        for record in self:
            if record.timeout < 10 or record.timeout > 600:
                raise ValidationError(_('El timeout debe estar entre 10 y 600 segundos'))

    def action_test_connection(self):
        """Prueba la conexión con OpenAI"""
        self.ensure_one()
        import requests
        import json

        try:
            headers = {
                'Authorization': f'Bearer {self.openai_api_key}',
                'Content-Type': 'application/json',
            }

            # Simple test request
            payload = {
                "model": self.openai_model,
                "input": "Responde solo: OK",
                "max_output_tokens": 20,
            }

            response = requests.post(
                self.openai_api_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=30
            )

            if response.status_code == 200:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Conexión Exitosa'),
                        'message': _('La conexión con OpenAI se realizó correctamente.'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                error_msg = response.json().get('error', {}).get('message', response.text)
                raise ValidationError(_('Error de OpenAI: %s') % error_msg)

        except requests.exceptions.Timeout:
            raise ValidationError(_('Timeout: No se pudo conectar con OpenAI'))
        except requests.exceptions.RequestException as e:
            raise ValidationError(_('Error de conexión: %s') % str(e))
