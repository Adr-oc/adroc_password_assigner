# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
import base64
import io
import json
import logging
import requests

_logger = logging.getLogger(__name__)

# PDF to Image conversion - optional dependencies
try:
    from pdf2image import convert_from_bytes
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False
    _logger.warning('pdf2image not available. PDF support will be limited.')

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# PDF table extraction - optional dependency
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    _logger.warning('pdfplumber not available. Table extraction will use AI only.')

import re


class PasswordAssignerWizard(models.TransientModel):
    _name = 'password.assigner.wizard'
    _description = 'Wizard de Asignación de Contraseñas'

    # State management
    state = fields.Selection([
        ('upload', 'Subir Documentos'),
        ('processing', 'Procesando'),
        ('preview', 'Preview'),
        ('done', 'Finalizado'),
    ], string='Estado',
        default='upload',
        required=True
    )

    # Document upload
    document_ids = fields.Many2many(
        'ir.attachment',
        'password_assigner_wizard_attachment_rel',
        'wizard_id',
        'attachment_id',
        string='Documentos',
        help='Documentos a procesar (imágenes, PDFs, Excel)'
    )

    # Configuration
    config_id = fields.Many2one(
        'password.assigner.config',
        string='Configuración IA',
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        help='Configuración de OpenAI para procesamiento de imágenes/PDFs'
    )
    template_id = fields.Many2one(
        'password.assigner.template',
        string='Plantilla Excel',
        help='Plantilla para procesar archivos Excel (opcional)'
    )
    company_id = fields.Many2one(
        'res.company',
        string='Compañía',
        default=lambda self: self.env.company,
        required=True
    )

    # Preview lines
    line_ids = fields.One2many(
        'password.assigner.wizard.line',
        'wizard_id',
        string='Líneas de Asignación'
    )

    # Processing info
    error_message = fields.Text(
        string='Errores',
        readonly=True
    )
    processing_log = fields.Text(
        string='Log de Procesamiento',
        readonly=True
    )

    # Statistics (computed)
    total_documents = fields.Integer(
        string='Documentos',
        compute='_compute_statistics'
    )
    total_passwords = fields.Integer(
        string='Contraseñas',
        compute='_compute_statistics'
    )
    total_matched = fields.Integer(
        string='Con Match',
        compute='_compute_statistics'
    )
    total_unmatched = fields.Integer(
        string='Sin Match',
        compute='_compute_statistics'
    )
    total_to_apply = fields.Integer(
        string='A Aplicar',
        compute='_compute_statistics'
    )

    @api.depends('document_ids', 'line_ids', 'line_ids.apply', 'line_ids.match_status', 'line_ids.invoice_ids')
    def _compute_statistics(self):
        for wizard in self:
            wizard.total_documents = len(wizard.document_ids)
            wizard.total_passwords = len(wizard.line_ids)
            wizard.total_matched = len(wizard.line_ids.filtered(lambda l: l.invoice_ids))
            wizard.total_unmatched = len(wizard.line_ids.filtered(lambda l: not l.invoice_ids))
            wizard.total_to_apply = len(wizard.line_ids.filtered(lambda l: l.apply and l.invoice_ids))

    @api.onchange('document_ids')
    def _onchange_document_ids(self):
        """Detecta si hay archivos Excel para mostrar campo de plantilla"""
        has_excel = any(
            att.name and att.name.lower().endswith(('.xlsx', '.xls', '.csv'))
            for att in self.document_ids
        )
        if has_excel and not self.template_id:
            # Suggest user to select a template
            pass

    def action_process_documents(self):
        """Procesa los documentos subidos y genera líneas de preview"""
        self.ensure_one()

        if not self.document_ids:
            raise UserError(_('Debe subir al menos un documento.'))

        self.state = 'processing'
        self.error_message = ''
        self.processing_log = ''
        errors = []
        log_lines = []

        # Clear existing lines
        self.line_ids.unlink()

        for attachment in self.document_ids:
            try:
                filename = attachment.name or ''
                file_content = base64.b64decode(attachment.datas)
                mime_type = attachment.mimetype or self._guess_mimetype(filename)

                log_lines.append(f"Procesando: {filename} ({mime_type})")

                if self._is_excel_file(filename, mime_type):
                    # Process Excel with template
                    results = self._process_excel(attachment, file_content, filename)
                elif self._is_image_or_pdf(filename, mime_type):
                    # Process image/PDF with OpenAI
                    results = self._process_image_pdf(attachment, file_content, filename, mime_type)
                else:
                    errors.append(f"Tipo de archivo no soportado: {filename}")
                    continue

                # Create preview lines from results
                for result in results:
                    self._create_preview_line(result, filename)

                log_lines.append(f"  -> {len(results)} contraseñas encontradas")

            except Exception as e:
                error_msg = f"Error procesando {attachment.name}: {str(e)}"
                errors.append(error_msg)
                log_lines.append(f"  -> ERROR: {str(e)}")
                _logger.exception(error_msg)

        self.processing_log = '\n'.join(log_lines)
        if errors:
            self.error_message = '\n'.join(errors)

        self.state = 'preview'

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _is_excel_file(self, filename, mime_type):
        """Verifica si es un archivo Excel"""
        excel_extensions = ('.xlsx', '.xls', '.csv')
        excel_mimetypes = (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.ms-excel',
            'text/csv',
        )
        return (
            filename.lower().endswith(excel_extensions) or
            mime_type in excel_mimetypes
        )

    def _is_image_or_pdf(self, filename, mime_type):
        """Verifica si es una imagen o PDF"""
        image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.tiff', '.tif', '.bmp')
        return (
            filename.lower().endswith(image_extensions) or
            filename.lower().endswith('.pdf') or
            mime_type.startswith('image/') or
            mime_type == 'application/pdf'
        )

    def _guess_mimetype(self, filename):
        """Adivina el MIME type basado en la extensión"""
        ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
        return {
            'pdf': 'application/pdf',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'webp': 'image/webp',
            'tif': 'image/tiff',
            'tiff': 'image/tiff',
            'bmp': 'image/bmp',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'xls': 'application/vnd.ms-excel',
            'csv': 'text/csv',
        }.get(ext, 'application/octet-stream')

    def _process_excel(self, attachment, file_content, filename):
        """Procesa archivo Excel usando plantilla"""
        if not self.template_id:
            raise UserError(_(
                'Debe seleccionar una plantilla para procesar archivos Excel.\n'
                'Archivo: %s'
            ) % filename)

        parsed_data = self.template_id.parse_file(file_content, filename)

        # Group by password
        passwords = {}
        for row in parsed_data:
            pwd = row.get('password', '') or 'SIN_CONTRASEÑA'
            if pwd not in passwords:
                passwords[pwd] = {
                    'password_number': pwd if pwd != 'SIN_CONTRASEÑA' else '',
                    'issuer_name': '',
                    'invoices': [],
                }
            passwords[pwd]['invoices'].append({
                'invoice_number': row.get('invoice_number', ''),
                'invoice_series': row.get('invoice_series', ''),
                'amount': row.get('amount', 0),
                'date': row.get('date', ''),
            })

        return [
            {
                'password_number': data['password_number'],
                'issuer_name': data['issuer_name'],
                'invoices': data['invoices'],
                'source': 'excel',
            }
            for data in passwords.values()
        ]

    def _extract_tables_from_pdf(self, file_content, filename):
        """
        Extrae tablas de un PDF usando pdfplumber.
        Retorna lista de contraseñas con sus facturas si encuentra tablas válidas.
        """
        if not PDFPLUMBER_AVAILABLE:
            return None

        try:
            pdf_buffer = io.BytesIO(file_content)
            password_number = None
            issuer_name = None
            all_invoices = []

            with pdfplumber.open(pdf_buffer) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    # Extraer texto de la página para buscar contraseña y emisor
                    page_text = page.extract_text() or ''

                    # Buscar número de contraseña en el texto
                    if not password_number:
                        # Patrones comunes: "No. DIS - 5994", "Contraseña: 055648", "No. 12345"
                        pwd_patterns = [
                            r'No\.\s*([A-Z]{2,4}\s*-?\s*\d+)',  # DIS - 5994, CAR-1234
                            r'Contraseña[:\s]+(\d+)',
                            r'Nº?\.\s*(\d+)',
                            r'No\.\s+(\d+)',
                        ]
                        for pattern in pwd_patterns:
                            match = re.search(pattern, page_text, re.IGNORECASE)
                            if match:
                                password_number = match.group(1).strip()
                                break

                    # Buscar nombre del emisor
                    if not issuer_name:
                        issuer_patterns = [
                            r'(DISTELSA|CARTOGUA|La Popular|Carton Box|GRUPO\s+\w+)',
                            r'Contraseña de pago\s+([A-Z][A-Za-z\s]+)',
                        ]
                        for pattern in issuer_patterns:
                            match = re.search(pattern, page_text, re.IGNORECASE)
                            if match:
                                issuer_name = match.group(1).strip()
                                break

                    # Extraer tablas de la página
                    tables = page.extract_tables()

                    for table in tables:
                        if not table or len(table) < 2:
                            continue

                        # Detectar columnas de factura y monto
                        header = table[0] if table[0] else []
                        header_lower = [str(h).lower() if h else '' for h in header]

                        # Buscar índices de columnas
                        factura_idx = None
                        monto_idx = None

                        for i, h in enumerate(header_lower):
                            if 'factura' in h or 'número' in h or 'no.' in h:
                                factura_idx = i
                            if 'monto' in h or 'total' in h or 'importe' in h:
                                monto_idx = i

                        # Si no encontró headers, intentar detectar por posición
                        # Típicamente: # | Factura | Monto
                        if factura_idx is None and len(header) >= 2:
                            # Asumir segunda columna es factura
                            factura_idx = 1
                        if monto_idx is None and len(header) >= 3:
                            # Asumir última columna es monto
                            monto_idx = len(header) - 1

                        if factura_idx is None:
                            continue

                        # Procesar filas de datos (saltar header)
                        for row in table[1:]:
                            if not row or len(row) <= factura_idx:
                                continue

                            invoice_num = str(row[factura_idx] or '').strip()
                            if not invoice_num or invoice_num.lower() in ['', 'none', 'null', 'factura']:
                                continue

                            # Extraer monto si existe
                            amount = 0.0
                            if monto_idx is not None and len(row) > monto_idx:
                                monto_str = str(row[monto_idx] or '').strip()
                                # Limpiar formato de número: "1,606.58" -> 1606.58
                                monto_clean = re.sub(r'[^\d.,]', '', monto_str)
                                monto_clean = monto_clean.replace(',', '')
                                try:
                                    amount = float(monto_clean) if monto_clean else 0.0
                                except ValueError:
                                    amount = 0.0

                            all_invoices.append({
                                'invoice_number': invoice_num,
                                'invoice_series': None,
                                'amount': amount,
                                'currency': 'Q',
                                'date': None,
                            })

            # Si encontramos facturas, retornar resultado
            if all_invoices and password_number:
                _logger.info('Extracted %d invoices from PDF tables, password: %s',
                           len(all_invoices), password_number)
                return [{
                    'password_number': password_number,
                    'issuer_name': issuer_name or '',
                    'invoices': all_invoices,
                    'source': 'table_extraction',
                    'confidence': 95,
                }]

            # Si encontramos facturas pero no contraseña, intentar extraer del nombre del archivo
            if all_invoices and not password_number:
                # Intentar extraer del nombre: "DIS-5994- MEGAPOLIZAS.pdf"
                match = re.search(r'([A-Z]{2,4}-?\d+)', filename)
                if match:
                    password_number = match.group(1)
                    _logger.info('Extracted password from filename: %s', password_number)
                    return [{
                        'password_number': password_number,
                        'issuer_name': issuer_name or '',
                        'invoices': all_invoices,
                        'source': 'table_extraction',
                        'confidence': 85,
                    }]

            return None

        except Exception as e:
            _logger.warning('Error extracting tables from PDF: %s', str(e))
            return None

    def _process_image_pdf(self, attachment, file_content, filename, mime_type):
        """Procesa imagen o PDF - primero intenta OCR/tabla, luego IA"""

        # Para PDFs, intentar primero extracción de tablas (más rápido y preciso)
        if mime_type == 'application/pdf' and PDFPLUMBER_AVAILABLE:
            _logger.info('Attempting table extraction with pdfplumber for: %s', filename)
            table_results = self._extract_tables_from_pdf(file_content, filename)
            if table_results:
                _logger.info('Successfully extracted %d passwords from PDF tables', len(table_results))
                return table_results
            _logger.info('No tables found or extraction failed, falling back to AI')

        # Fallback a IA
        if not self.config_id:
            raise UserError(_(
                'Debe seleccionar una configuración de IA para procesar imágenes/PDFs.\n'
                'Archivo: %s'
            ) % filename)

        # Call OpenAI API
        response_data = self._call_openai_extraction(file_content, filename, mime_type)

        if not response_data:
            return []

        # Parse response
        passwords = response_data.get('passwords', [])
        results = []

        for pwd_data in passwords:
            results.append({
                'password_number': pwd_data.get('password_number', ''),
                'issuer_name': pwd_data.get('issuer_name', ''),
                'document_date': pwd_data.get('document_date', ''),
                'payment_date': pwd_data.get('payment_date', ''),
                'page_numbers': pwd_data.get('page_numbers', []),
                'invoices': pwd_data.get('invoices', []),
                'confidence': response_data.get('confidence', 0),
                'source': 'ai',
            })

        return results

    def _convert_pdf_to_images(self, pdf_content):
        """
        Convierte un PDF a lista de imágenes base64.
        Esto es necesario porque la Responses API tiene bugs con PDFs escaneados.
        """
        if not PDF2IMAGE_AVAILABLE:
            raise UserError(_(
                'La librería pdf2image no está instalada.\n'
                'Ejecute: pip install pdf2image\n'
                'También necesita poppler-utils: apt-get install poppler-utils'
            ))

        if not PIL_AVAILABLE:
            raise UserError(_('La librería Pillow no está instalada. Ejecute: pip install Pillow'))

        try:
            # Convertir PDF a imágenes (100 DPI para balance velocidad/calidad)
            images = convert_from_bytes(pdf_content, dpi=100, fmt='jpeg')

            result = []
            for i, img in enumerate(images):
                # Redimensionar si es muy grande (max 1500px de ancho)
                max_width = 1500
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_size = (max_width, int(img.height * ratio))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)

                # Convertir imagen a base64 (calidad 75 para reducir tamaño)
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=75)
                img_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                result.append({
                    'page': i + 1,
                    'base64': img_b64,
                    'mime': 'image/jpeg'
                })

            _logger.info('PDF converted to %d images', len(result))
            return result

        except Exception as e:
            _logger.exception('Error converting PDF to images')
            raise UserError(_('Error al convertir PDF a imágenes: %s') % str(e))

    def _call_openai_extraction(self, file_content, filename, mime_type):
        """Llama a OpenAI API para extraer información del documento"""
        config = self.config_id

        # Prepare content blocks
        content_blocks = []

        if mime_type.startswith('image/'):
            # Imagen directa
            data_b64 = base64.b64encode(file_content).decode('utf-8')
            content_blocks.append({
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{data_b64}"
            })
        elif mime_type == 'application/pdf':
            # Convertir PDF a imágenes para evitar bugs de la Responses API con PDFs
            _logger.info('Converting PDF to images for better OCR support...')
            pdf_images = self._convert_pdf_to_images(file_content)

            # Agregar cada página como imagen (máximo 10 páginas por request)
            max_pages = min(len(pdf_images), 10)
            if len(pdf_images) > 10:
                _logger.warning('PDF has %d pages, only processing first 10', len(pdf_images))

            for img_data in pdf_images[:max_pages]:
                content_blocks.append({
                    "type": "input_image",
                    "image_url": f"data:{img_data['mime']};base64,{img_data['base64']}"
                })

        # Add text prompt with page context for multi-page
        page_context = ""
        if mime_type == 'application/pdf' and len(content_blocks) > 1:
            page_context = f"""

IMPORTANTE - DOCUMENTO MULTI-PÁGINA:
- Este documento tiene {len(content_blocks)} páginas
- Debes extraer TODAS las facturas de TODAS las páginas
- La tabla de facturas continúa en la página 2
- Combina todas las facturas bajo UNA sola contraseña (si es el mismo número)
- NO omitas ninguna fila de la tabla"""

        content_blocks.append({
            "type": "input_text",
            "text": f"Analiza este documento y extrae la información de contraseñas de pago y TODAS las facturas según las instrucciones.{page_context}"
        })

        # Build payload
        payload = {
            "model": config.openai_model,
            "instructions": config.openai_instructions or '',
            "input": [{
                "role": "user",
                "content": content_blocks
            }],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "password_extraction",
                    "schema": json.loads(config.json_schema)['schema'],
                    "strict": True
                }
            },
            # Aumentar tokens de salida para documentos con muchas facturas
            "max_output_tokens": 16000,
        }

        headers = {
            'Authorization': f'Bearer {config.openai_api_key}',
            'Content-Type': 'application/json',
        }

        _logger.info('Calling OpenAI API for file: %s', filename)

        try:
            response = requests.post(
                config.openai_api_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=config.timeout
            )

            if response.status_code != 200:
                error_msg = response.json().get('error', {}).get('message', response.text)
                _logger.error('OpenAI API error: %s', error_msg)
                raise UserError(_('Error de OpenAI: %s') % error_msg)

            resp_json = response.json()

            # Extract content from response
            content_txt = resp_json.get('output_text')
            if not content_txt:
                for blk in resp_json.get('output', []):
                    for p in blk.get('content', []):
                        if p.get('type') in ('output_text', 'text') and p.get('text'):
                            content_txt = p['text']
                            break
                    if content_txt:
                        break

            if content_txt:
                return json.loads(content_txt)

            _logger.warning('No content extracted from OpenAI response')
            return None

        except requests.exceptions.Timeout:
            raise UserError(_('Timeout: No se pudo conectar con OpenAI'))
        except requests.exceptions.RequestException as e:
            raise UserError(_('Error de conexión: %s') % str(e))
        except json.JSONDecodeError as e:
            _logger.error('Error parsing OpenAI response: %s', str(e))
            raise UserError(_('Error al parsear respuesta de OpenAI'))

    def _create_preview_line(self, result, source_document):
        """Crea una línea de preview basada en los resultados extraídos"""
        password_number = result.get('password_number', '')
        if not password_number:
            return

        invoices = result.get('invoices', [])
        page_numbers = result.get('page_numbers', [])

        for inv_data in invoices:
            invoice_number = inv_data.get('invoice_number', '')
            invoice_series = inv_data.get('invoice_series', '')
            amount = inv_data.get('amount', 0)

            if not invoice_number:
                continue

            # Search for matching invoices
            matched_invoices, match_status, confidence = self._match_invoices(
                invoice_number, invoice_series, amount
            )

            # Build notes
            notes = []
            if result.get('source') == 'ai':
                notes.append(f"Confianza IA: {result.get('confidence', 0):.0f}%")
            if match_status == 'multiple':
                notes.append(f"Múltiples coincidencias encontradas ({len(matched_invoices)})")
            elif match_status == 'not_found':
                notes.append("No se encontró factura coincidente")

            self.env['password.assigner.wizard.line'].create({
                'wizard_id': self.id,
                'password': password_number,
                'issuer_name': result.get('issuer_name', ''),
                'source_document': source_document,
                'source_page': page_numbers[0] if page_numbers else 0,
                'invoice_number_extracted': invoice_number,
                'invoice_series_extracted': invoice_series,
                'amount_extracted': amount or 0,
                'invoice_ids': [(6, 0, matched_invoices.ids)] if matched_invoices else [],
                'match_confidence': confidence,
                'match_status': match_status,
                'apply': match_status in ('matched', 'partial') and bool(matched_invoices),
                'notes': '\n'.join(notes) if notes else '',
            })

    def _match_invoices(self, invoice_number, invoice_series, amount):
        """
        Busca facturas que coincidan con los datos extraídos.
        Match parcial: busca si el número extraído aparece en cualquier parte de invoice_number.
        También busca en las líneas de factura (descripción del producto).

        Returns:
            tuple: (matched_invoices recordset, match_status, confidence)
        """
        AccountMove = self.env['account.move']
        AccountMoveLine = self.env['account.move.line']

        base_domain = [
            ('move_type', 'in', ['out_invoice', 'out_refund']),
            ('state', '=', 'posted'),
            ('company_id', '=', self.company_id.id),
            # Solo facturas sin contraseña asignada
            '|',
            ('document_password', '=', False),
            ('document_password', '=', ''),
        ]

        # Clean invoice number for search
        clean_number = invoice_number.strip()

        # 1. Try exact match in invoice fields first
        exact_domain = base_domain + [
            '|', '|', '|',
            ('invoice_number', '=', clean_number),
            ('invoice_number', 'ilike', clean_number),
            ('name', 'ilike', clean_number),
            ('ref', 'ilike', clean_number),
        ]

        matched = AccountMove.search(exact_domain, limit=10)

        if len(matched) == 1:
            return matched, 'matched', 100.0

        # 2. If no match, search in invoice line descriptions (e.g., "POLTT2483374605")
        if not matched:
            # Search in invoice lines
            line_domain = [
                ('move_id.move_type', 'in', ['out_invoice', 'out_refund']),
                ('move_id.state', '=', 'posted'),
                ('move_id.company_id', '=', self.company_id.id),
                '|',
                ('move_id.document_password', '=', False),
                ('move_id.document_password', '=', ''),
                '|',
                ('name', 'ilike', clean_number),
                ('name', 'ilike', f'%{clean_number}%'),
            ]

            lines = AccountMoveLine.search(line_domain, limit=20)
            if lines:
                matched = lines.mapped('move_id')
                if len(matched) == 1:
                    return matched, 'matched', 95.0
                if matched:
                    # Filter by amount if available
                    if amount:
                        amount_matched = matched.filtered(
                            lambda m: abs(m.amount_total - amount) < 1.0
                        )
                        if len(amount_matched) == 1:
                            return amount_matched, 'matched', 90.0
                        if amount_matched:
                            matched = amount_matched

                    if len(matched) == 1:
                        return matched, 'matched', 85.0
                    return matched, 'multiple', 70.0

        if len(matched) > 1:
            # Try to narrow down with series
            if invoice_series:
                series_matched = matched.filtered(
                    lambda m: m.invoice_series and invoice_series in m.invoice_series
                )
                if len(series_matched) == 1:
                    return series_matched, 'matched', 95.0
                if series_matched:
                    matched = series_matched

            # Try to narrow down with amount
            if amount:
                amount_matched = matched.filtered(
                    lambda m: abs(m.amount_total - amount) < 1.0
                )
                if len(amount_matched) == 1:
                    return amount_matched, 'matched', 90.0
                if amount_matched:
                    matched = amount_matched

            return matched, 'multiple', 70.0

        # Try partial match - number contains extracted value
        partial_domain = base_domain + [
            '|', '|',
            ('invoice_number', 'ilike', f'%{clean_number}%'),
            ('name', 'ilike', f'%{clean_number}%'),
            ('ref', 'ilike', f'%{clean_number}%'),
        ]

        partial_matched = AccountMove.search(partial_domain, limit=10)

        if len(partial_matched) == 1:
            return partial_matched, 'partial', 80.0

        if partial_matched:
            # Filter by series if available
            if invoice_series:
                series_matched = partial_matched.filtered(
                    lambda m: m.invoice_series and invoice_series in m.invoice_series
                )
                if series_matched:
                    partial_matched = series_matched

            if len(partial_matched) == 1:
                return partial_matched, 'partial', 75.0

            return partial_matched, 'multiple', 60.0

        # No match found
        return AccountMove, 'not_found', 0.0

    def action_apply_passwords(self):
        """Aplica las contraseñas a las facturas seleccionadas"""
        self.ensure_one()

        lines_to_apply = self.line_ids.filtered(lambda l: l.apply and l.invoice_ids)

        if not lines_to_apply:
            raise UserError(_('No hay líneas seleccionadas para aplicar.'))

        applied_count = 0
        invoice_count = 0

        for line in lines_to_apply:
            for invoice in line.invoice_ids:
                invoice.write({'document_password': line.password})
                invoice_count += 1
            applied_count += 1

        self.state = 'done'
        self.processing_log = (self.processing_log or '') + f'\n\n✓ Aplicadas {applied_count} contraseñas a {invoice_count} facturas.'

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_back_to_upload(self):
        """Vuelve al estado de upload"""
        self.ensure_one()
        self.state = 'upload'
        self.line_ids.unlink()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_close(self):
        """Cierra el wizard"""
        return {'type': 'ir.actions.act_window_close'}

    def action_select_all(self):
        """Selecciona todas las líneas con facturas"""
        self.line_ids.filtered(lambda l: l.invoice_ids).write({'apply': True})
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_deselect_all(self):
        """Deselecciona todas las líneas"""
        self.line_ids.write({'apply': False})
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
