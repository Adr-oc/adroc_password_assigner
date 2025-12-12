# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


class PasswordAssignerWizardLine(models.TransientModel):
    _name = 'password.assigner.wizard.line'
    _description = 'Línea de Preview de Asignación de Contraseña'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'password.assigner.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade'
    )
    sequence = fields.Integer(
        string='Secuencia',
        default=10
    )

    # Password info
    password = fields.Char(
        string='Contraseña',
        required=True,
        help='Contraseña a asignar a las facturas'
    )
    issuer_name = fields.Char(
        string='Emisor',
        help='Nombre de la empresa que emite la contraseña'
    )

    # Source document info
    source_document = fields.Char(
        string='Documento Fuente',
        help='Nombre del documento de donde se extrajo la información'
    )
    source_page = fields.Integer(
        string='Página',
        help='Número de página del documento (si aplica)'
    )

    # Extracted invoice info (for reference)
    invoice_number_extracted = fields.Char(
        string='Número Extraído',
        help='Número de factura extraído del documento'
    )
    invoice_series_extracted = fields.Char(
        string='Serie Extraída',
        help='Serie de factura extraída del documento'
    )
    amount_extracted = fields.Float(
        string='Monto Extraído',
        digits='Product Price',
        help='Monto extraído del documento (para validación)'
    )

    # Matched invoices
    invoice_ids = fields.Many2many(
        'account.move',
        'password_assigner_line_invoice_rel',
        'line_id',
        'invoice_id',
        string='Facturas',
        domain="[('move_type', 'in', ['out_invoice', 'out_refund'])]",
        help='Facturas a las que se asignará la contraseña'
    )
    invoice_count = fields.Integer(
        string='Cantidad',
        compute='_compute_invoice_count'
    )

    # Match quality
    match_confidence = fields.Float(
        string='Confianza (%)',
        digits=(5, 1),
        help='Nivel de confianza del match (0-100)'
    )
    match_status = fields.Selection([
        ('matched', 'Coincidencia Exacta'),
        ('partial', 'Coincidencia Parcial'),
        ('multiple', 'Múltiples Coincidencias'),
        ('not_found', 'No Encontrada'),
        ('manual', 'Selección Manual'),
    ], string='Estado del Match',
        default='not_found',
        help='Estado del proceso de búsqueda de facturas'
    )

    # Apply control
    apply = fields.Boolean(
        string='Aplicar',
        default=True,
        help='Marcar para aplicar esta asignación'
    )
    notes = fields.Text(
        string='Notas',
        help='Notas o advertencias sobre esta línea'
    )

    # Related fields for display
    invoice_partners = fields.Char(
        string='Clientes',
        compute='_compute_invoice_info'
    )
    invoice_amounts = fields.Char(
        string='Montos',
        compute='_compute_invoice_info'
    )
    invoice_numbers_display = fields.Char(
        string='Números de Factura',
        compute='_compute_invoice_info'
    )

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for line in self:
            line.invoice_count = len(line.invoice_ids)

    @api.depends('invoice_ids', 'invoice_ids.partner_id', 'invoice_ids.amount_total', 'invoice_ids.invoice_number')
    def _compute_invoice_info(self):
        for line in self:
            if line.invoice_ids:
                # Partners
                partners = line.invoice_ids.mapped('partner_id.name')
                line.invoice_partners = ', '.join(set(partners)) if partners else ''

                # Amounts
                amounts = [f"{inv.currency_id.symbol}{inv.amount_total:,.2f}" for inv in line.invoice_ids]
                line.invoice_amounts = ', '.join(amounts) if amounts else ''

                # Invoice numbers
                numbers = []
                for inv in line.invoice_ids:
                    if inv.invoice_series and inv.invoice_number:
                        numbers.append(f"{inv.invoice_series}-{inv.invoice_number}")
                    elif inv.invoice_number:
                        numbers.append(inv.invoice_number)
                    else:
                        numbers.append(inv.name or '')
                line.invoice_numbers_display = ', '.join(numbers) if numbers else ''
            else:
                line.invoice_partners = ''
                line.invoice_amounts = ''
                line.invoice_numbers_display = ''

    @api.onchange('invoice_ids')
    def _onchange_invoice_ids(self):
        """Actualiza el estado del match cuando se modifican las facturas"""
        if self.invoice_ids:
            if self.match_status == 'not_found':
                self.match_status = 'manual'
                self.apply = True
        else:
            if self.match_status not in ['not_found']:
                self.notes = (self.notes or '') + '\nFacturas removidas manualmente.'

    @api.onchange('invoice_number_extracted')
    def _onchange_invoice_number_extracted(self):
        """Busca facturas automáticamente cuando se cambia el número extraído"""
        if not self.invoice_number_extracted:
            return

        clean_number = self.invoice_number_extracted.strip()
        if len(clean_number) < 3:
            return  # Muy corto para buscar

        company_id = self.wizard_id.company_id.id if self.wizard_id else self.env.company.id

        # Buscar en campos de factura
        AccountMove = self.env['account.move']
        AccountMoveLine = self.env['account.move.line']

        base_domain = [
            ('move_type', 'in', ['out_invoice', 'out_refund']),
            ('state', '=', 'posted'),
            ('company_id', '=', company_id),
            '|',
            ('document_password', '=', False),
            ('document_password', '=', ''),
        ]

        # Buscar en invoice_number, name, ref
        search_domain = base_domain + [
            '|', '|', '|',
            ('invoice_number', 'ilike', clean_number),
            ('name', 'ilike', clean_number),
            ('ref', 'ilike', clean_number),
            ('invoice_number', 'ilike', f'%{clean_number}%'),
        ]

        matched = AccountMove.search(search_domain, limit=10)

        # Si no encontró, buscar en líneas de factura
        if not matched:
            line_domain = [
                ('move_id.move_type', 'in', ['out_invoice', 'out_refund']),
                ('move_id.state', '=', 'posted'),
                ('move_id.company_id', '=', company_id),
                '|',
                ('move_id.document_password', '=', False),
                ('move_id.document_password', '=', ''),
                ('name', 'ilike', clean_number),
            ]
            lines = AccountMoveLine.search(line_domain, limit=10)
            if lines:
                matched = lines.mapped('move_id')

        if matched:
            # Si hay monto, filtrar por monto similar
            if self.amount_extracted and len(matched) > 1:
                amount_matched = matched.filtered(
                    lambda m: abs(m.amount_total - self.amount_extracted) < 1.0
                )
                if amount_matched:
                    matched = amount_matched

            self.invoice_ids = [(6, 0, matched.ids)]
            self.match_status = 'manual'
            self.apply = True
            self.match_confidence = 85.0

            if len(matched) == 1:
                self.notes = f"✓ Encontrada: {matched.name}"
            else:
                self.notes = f"Encontradas {len(matched)} facturas. Verifica cuál es la correcta."
        else:
            self.invoice_ids = [(5, 0, 0)]  # Clear
            self.match_status = 'not_found'
            self.apply = False
            self.notes = f"No se encontró factura para: {clean_number}"

    def action_open_invoices(self):
        """Abre las facturas relacionadas en una vista"""
        self.ensure_one()
        if not self.invoice_ids:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sin Facturas'),
                    'message': _('No hay facturas asignadas a esta línea.'),
                    'type': 'warning',
                    'sticky': False,
                }
            }

        return {
            'type': 'ir.actions.act_window',
            'name': _('Facturas - %s') % self.password,
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.invoice_ids.ids)],
            'context': {'create': False},
        }
