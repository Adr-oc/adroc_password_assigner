# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import logging

_logger = logging.getLogger(__name__)


class PasswordAssignerTemplate(models.Model):
    _name = 'password.assigner.template'
    _description = 'Plantilla de Parsing para Asignador de Contraseñas'
    _order = 'sequence, name'

    name = fields.Char(
        string='Nombre',
        required=True,
        help='Nombre descriptivo de la plantilla (ej: "Formato CARTOGUA")'
    )
    description = fields.Text(
        string='Descripción',
        help='Descripción detallada del formato de archivo'
    )
    sequence = fields.Integer(
        string='Secuencia',
        default=10
    )
    active = fields.Boolean(
        string='Activo',
        default=True
    )

    file_type = fields.Selection([
        ('excel', 'Excel (.xlsx, .xls)'),
        ('csv', 'CSV (.csv)'),
    ], string='Tipo de Archivo',
        default='excel',
        required=True,
        help='Tipo de archivo que procesa esta plantilla'
    )

    # Column configuration for Excel/CSV
    column_password = fields.Char(
        string='Columna Contraseña',
        help='Nombre de la columna que contiene la contraseña de pago'
    )
    column_invoice_number = fields.Char(
        string='Columna Número Factura',
        required=True,
        help='Nombre de la columna que contiene el número de factura'
    )
    column_invoice_series = fields.Char(
        string='Columna Serie Factura',
        help='Nombre de la columna que contiene la serie de factura (opcional)'
    )
    column_amount = fields.Char(
        string='Columna Monto',
        help='Nombre de la columna que contiene el monto (opcional, para validación)'
    )
    column_date = fields.Char(
        string='Columna Fecha',
        help='Nombre de la columna que contiene la fecha (opcional)'
    )

    # Parsing options
    skip_rows = fields.Integer(
        string='Filas a Saltar',
        default=0,
        help='Número de filas a saltar al inicio del archivo (antes de los encabezados)'
    )
    header_row = fields.Integer(
        string='Fila de Encabezados',
        default=0,
        help='Número de fila que contiene los encabezados (0 = primera fila después de skip_rows)'
    )
    sheet_name = fields.Char(
        string='Nombre de Hoja',
        help='Nombre de la hoja de Excel a leer (dejar vacío para la primera hoja)'
    )
    sheet_index = fields.Integer(
        string='Índice de Hoja',
        default=0,
        help='Índice de la hoja de Excel (0 = primera hoja)'
    )

    # Password grouping
    password_mode = fields.Selection([
        ('single_column', 'Una columna de contraseña'),
        ('one_per_row', 'Una contraseña por fila'),
        ('grouped', 'Contraseña agrupada (varias filas por contraseña)'),
    ], string='Modo de Contraseña',
        default='single_column',
        required=True,
        help='Cómo se estructura la contraseña en el archivo'
    )

    # Sample file for reference
    sample_file = fields.Binary(
        string='Archivo de Ejemplo',
        help='Archivo de ejemplo para referencia'
    )
    sample_filename = fields.Char(
        string='Nombre Archivo Ejemplo'
    )

    @api.constrains('column_invoice_number')
    def _check_column_invoice_number(self):
        for record in self:
            if not record.column_invoice_number:
                raise ValidationError(_('Debe especificar la columna de número de factura'))

    @api.constrains('skip_rows', 'header_row', 'sheet_index')
    def _check_positive_integers(self):
        for record in self:
            if record.skip_rows < 0:
                raise ValidationError(_('Las filas a saltar no pueden ser negativas'))
            if record.header_row < 0:
                raise ValidationError(_('La fila de encabezados no puede ser negativa'))
            if record.sheet_index < 0:
                raise ValidationError(_('El índice de hoja no puede ser negativo'))

    def parse_file(self, file_content, filename):
        """
        Parsea un archivo usando la configuración de esta plantilla.

        Args:
            file_content: Contenido del archivo en bytes
            filename: Nombre del archivo

        Returns:
            list: Lista de diccionarios con los datos extraídos
        """
        self.ensure_one()
        import pandas as pd
        import io

        try:
            if self.file_type == 'excel':
                # Determinar sheet
                sheet = self.sheet_name if self.sheet_name else self.sheet_index

                df = pd.read_excel(
                    io.BytesIO(file_content),
                    sheet_name=sheet,
                    skiprows=self.skip_rows,
                    header=self.header_row,
                    engine='openpyxl'
                )
            elif self.file_type == 'csv':
                df = pd.read_csv(
                    io.BytesIO(file_content),
                    skiprows=self.skip_rows,
                    header=self.header_row
                )
            else:
                raise ValidationError(_('Tipo de archivo no soportado: %s') % self.file_type)

            # Validar columnas requeridas
            if self.column_invoice_number not in df.columns:
                available_cols = ', '.join(df.columns.tolist())
                raise ValidationError(
                    _('Columna "%s" no encontrada. Columnas disponibles: %s') %
                    (self.column_invoice_number, available_cols)
                )

            results = []
            current_password = None

            for idx, row in df.iterrows():
                invoice_number = str(row.get(self.column_invoice_number, '')).strip()
                if not invoice_number or invoice_number == 'nan':
                    continue

                # Obtener contraseña
                if self.column_password and self.column_password in df.columns:
                    password_val = row.get(self.column_password, '')
                    if password_val and str(password_val) != 'nan':
                        current_password = str(password_val).strip()

                # Obtener serie
                invoice_series = ''
                if self.column_invoice_series and self.column_invoice_series in df.columns:
                    series_val = row.get(self.column_invoice_series, '')
                    if series_val and str(series_val) != 'nan':
                        invoice_series = str(series_val).strip()

                # Obtener monto
                amount = 0.0
                if self.column_amount and self.column_amount in df.columns:
                    amount_val = row.get(self.column_amount, 0)
                    try:
                        amount = float(amount_val) if amount_val and str(amount_val) != 'nan' else 0.0
                    except (ValueError, TypeError):
                        amount = 0.0

                # Obtener fecha
                date_val = ''
                if self.column_date and self.column_date in df.columns:
                    date_raw = row.get(self.column_date, '')
                    if date_raw and str(date_raw) != 'nan':
                        date_val = str(date_raw)

                results.append({
                    'password': current_password or '',
                    'invoice_number': invoice_number,
                    'invoice_series': invoice_series,
                    'amount': amount,
                    'date': date_val,
                    'row_index': idx,
                })

            _logger.info('Template %s parsed %d rows from %s', self.name, len(results), filename)
            return results

        except Exception as e:
            _logger.error('Error parsing file %s with template %s: %s', filename, self.name, str(e))
            raise ValidationError(_('Error al procesar el archivo: %s') % str(e))
