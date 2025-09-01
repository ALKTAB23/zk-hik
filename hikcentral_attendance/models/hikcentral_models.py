# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import json

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    hik_base_url = fields.Char(string='HikCentral Base URL')
    hik_app_key = fields.Char(string='Hik App Key')
    hik_app_secret = fields.Char(string='Hik App Secret')
    hik_timeout = fields.Integer(string='Hik Timeout (s)', default=20)
    hik_timezone = fields.Char(string='Hik Timezone', default='UTC')

    def set_values(self):
        res = super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('hikcentral.base_url', self.hik_base_url or '')
        params.set_param('hikcentral.app_key', self.hik_app_key or '')
        params.set_param('hikcentral.app_secret', self.hik_app_secret or '')
        params.set_param('hikcentral.timeout', self.hik_timeout)
        params.set_param('hikcentral.tz', self.hik_timezone or 'UTC')
        return res

    @api.model
    def get_values(self):
        res = super().get_values()
        params = self.env['ir.config_parameter'].sudo()
        res.update(
            hik_base_url=params.get_param('hikcentral.base_url', default=''),
            hik_app_key=params.get_param('hikcentral.app_key', default=''),
            hik_app_secret=params.get_param('hikcentral.app_secret', default=''),
            hik_timeout=int(params.get_param('hikcentral.timeout', default='20')),
            hik_timezone=params.get_param('hikcentral.tz', default='UTC'),
        )
        return res

class HikImportLog(models.Model):
    _name = 'hik.import.log'
    _description = 'HikCentral Import Log'
    _order = 'create_date desc'

    name = fields.Char(default=lambda self: fields.Datetime.now())
    date_from = fields.Datetime()
    date_to = fields.Datetime()
    status = fields.Selection([
        ('success', 'Success'),
        ('partial', 'Partial'),
        ('failed', 'Failed')
    ], default='success')
    message = fields.Text()
    created = fields.Integer(string='Created Records', default=0)
    skipped = fields.Integer(string='Skipped Records', default=0)

class HikCentralService(models.AbstractModel):
    _name = 'hikcentral.service'
    _description = 'HikCentral API Service'

    def _get_conf(self):
        ICP = self.env['ir.config_parameter'].sudo()
        base = ICP.get_param('hikcentral.base_url')
        key = ICP.get_param('hikcentral.app_key')
        sec = ICP.get_param('hikcentral.app_secret')
        timeout = int(ICP.get_param('hikcentral.timeout', default='20'))
        if not base or not key or not sec:
            raise UserError(_('Configure HikCentral settings first.'))
        return base.rstrip('/'), key, sec, timeout

    def _fetch_attendance(self, date_from, date_to, page_no=1, page_size=100):
        # Placeholder: you should implement actual HikCentral API calls here.
        # Return list of dict events: [{employee_code, punch_time, punch_type}, ...]
        return []

    def import_range(self, date_from, date_to):
        log = self.env['hik.import.log'].create({
            'date_from': date_from,
            'date_to': date_to,
            'status': 'success',
            'message': '',
        })
        created = 0
        skipped = 0
        employees = self.env['hr.employee'].sudo()
        page = 1
        while True:
            events = self._fetch_attendance(date_from, date_to, page_no=page)
            if not events:
                break
            for ev in events:
                emp = employees.search([('barcode', '=', ev.get('employee_code'))], limit=1)
                if not emp:
                    skipped += 1
                    continue
                punch_dt = fields.Datetime.from_string(ev.get('punch_time'))
                # Deduplicate by employee + timestamp
                exists = self.env['hr.attendance'].sudo().search([
                    ('employee_id', '=', emp.id),
                    ('check_in', '=', punch_dt)
                ], limit=1)
                if exists:
                    skipped += 1
                    continue
                # Simple rule: even punches as check-in, odd as check-out
                # In practice, map HikCentral event type to punch in/out
                last_open = self.env['hr.attendance'].sudo().search([
                    ('employee_id', '=', emp.id),
                    ('check_out', '=', False)
                ], limit=1)
                if last_open:
                    last_open.write({'check_out': punch_dt})
                else:
                    self.env['hr.attendance'].sudo().create({
                        'employee_id': emp.id,
                        'check_in': punch_dt
                    })
                created += 1
            page += 1
        log.write({'created': created, 'skipped': skipped})
        return log
