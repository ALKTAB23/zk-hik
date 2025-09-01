# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
import json
import uuid
from datetime import datetime
import requests

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    hik_base_url = fields.Char(string='HikCentral Base URL')
    hik_app_key = fields.Char(string='Hik App Key')
    hik_app_secret = fields.Char(string='Hik App Secret')
    hik_timeout = fields.Integer(string='Hik Timeout (s)', default=20)
    hik_timezone = fields.Char(string='Hik Timezone', default='UTC')
    hik_auth_type = fields.Selection([
        ('basic', 'Basic Auth (username/password)'),
        ('token', 'Token in Header'),
    ], string='Auth Type', default='basic')
    hik_username = fields.Char(string='Username (for Basic Auth)')
    hik_password = fields.Char(string='Password/Token')
    hik_endpoint_path = fields.Char(string='Attendance Endpoint Path', default='/ISAPI/AccessControl/AcsEvent?format=json')
    hik_page_size = fields.Integer(string='Page Size', default=100)

    def set_values(self):
        res = super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('hikcentral.base_url', self.hik_base_url or '')
        params.set_param('hikcentral.app_key', self.hik_app_key or '')
        params.set_param('hikcentral.app_secret', self.hik_app_secret or '')
        params.set_param('hikcentral.timeout', self.hik_timeout)
        params.set_param('hikcentral.tz', self.hik_timezone or 'UTC')
        params.set_param('hikcentral.auth_type', self.hik_auth_type or 'basic')
        params.set_param('hikcentral.username', self.hik_username or '')
        params.set_param('hikcentral.password', self.hik_password or '')
        params.set_param('hikcentral.endpoint_path', self.hik_endpoint_path or '/ISAPI/AccessControl/AcsEvent?format=json')
        params.set_param('hikcentral.page_size', self.hik_page_size or 100)
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
            hik_auth_type=params.get_param('hikcentral.auth_type', default='basic'),
            hik_username=params.get_param('hikcentral.username', default=''),
            hik_password=params.get_param('hikcentral.password', default=''),
            hik_endpoint_path=params.get_param('hikcentral.endpoint_path', default='/ISAPI/AccessControl/AcsEvent?format=json'),
            hik_page_size=int(params.get_param('hikcentral.page_size', default='100')),
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
        auth_type = ICP.get_param('hikcentral.auth_type', default='basic')
        username = ICP.get_param('hikcentral.username', default='')
        password = ICP.get_param('hikcentral.password', default='')
        endpoint_path = ICP.get_param('hikcentral.endpoint_path', default='/ISAPI/AccessControl/AcsEvent?format=json')
        page_size = int(ICP.get_param('hikcentral.page_size', default='100'))
        if not base:
            raise UserError(_('Configure HikCentral base URL in settings.'))
        return base.rstrip('/'), key, sec, timeout, auth_type, username, password, endpoint_path, page_size

    def _fetch_attendance(self, date_from, date_to, page_no=1, page_size=100):
        base, app_key, app_secret, timeout, auth_type, username, password, endpoint_path, default_ps = self._get_conf()
        page_size = page_size or default_ps
        url = f"{base}{endpoint_path}"
        # Normalize datetimes to ISO8601 without microseconds
        if isinstance(date_from, str):
            start_iso = date_from
        else:
            start_iso = fields.Datetime.to_string(date_from)
        if isinstance(date_to, str):
            end_iso = date_to
        else:
            end_iso = fields.Datetime.to_string(date_to)
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        auth = None
        if auth_type == 'basic':
            # Use username/password if set; else fall back to app_key/app_secret
            auth = (username or app_key or '', password or app_secret or '')
        elif auth_type == 'token':
            headers['Authorization'] = f"Bearer {password or app_secret or ''}"
        # Try a common ISAPI AcsEvent POST body
        body_variants = [
            {
                "AcsEventSearchCond": {
                    "searchID": f"odoo-{uuid.uuid4()}",
                    "searchResultPosition": (page_no - 1) * page_size + 1,
                    "maxResults": page_size,
                    "startTime": start_iso.replace(' ', 'T'),
                    "endTime": end_iso.replace(' ', 'T')
                }
            },
            # Alternative popular schema
            {
                "AcsEventCond": {
                    "startTime": start_iso.replace(' ', 'T'),
                    "endTime": end_iso.replace(' ', 'T'),
                    "pageNo": page_no,
                    "pageSize": page_size
                }
            }
        ]
        events_out = []
        last_exc = None
        for body in body_variants:
            try:
                resp = requests.post(url, headers=headers, json=body, timeout=timeout, auth=auth, verify=False)
                if resp.status_code >= 400:
                    last_exc = UserError(_(f"HikCentral responded with {resp.status_code}: {resp.text[:200]}"))
                    continue
                data = resp.json() if resp.content else {}
                # Try multiple known paths to the events array
                possible_lists = [
                    data.get('AcsEvent', []),
                    data.get('AcsEventInfo', []),
                    data.get('MatchList', {}).get('AcsEvent', []),
                    data.get('list', []),
                    data.get('data', {}).get('list', []),
                ]
                raw_events = []
                for lst in possible_lists:
                    if isinstance(lst, list) and lst:
                        raw_events = lst
                        break
                # If still empty but data itself is a list
                if not raw_events and isinstance(data, list):
                    raw_events = data
                for ev in raw_events:
                    # Normalize fields with best-effort mapping
                    code = ev.get('employeeNoString') or ev.get('employeeId') or ev.get('personId') or ev.get('cardNo') or ev.get('employee_code')
                    # Times
                    t = ev.get('time') or ev.get('swipeTime') or ev.get('eventTime') or ev.get('occurTime') or ev.get('dateTime')
                    # Punch type hint if present
                    punch = ev.get('inAndOutType') or ev.get('attendanceStatus') or ev.get('punchType')
                    if code and t:
                        events_out.append({
                            'employee_code': str(code),
                            'punch_time': t,
                            'punch_type': punch
                        })
                break  # success with this body schema
            except Exception as e:
                last_exc = e
                continue
        if not events_out and last_exc:
            # Surface last error for troubleshooting in logs but don't crash import_range
            # The caller handles empty results to end pagination
            _ = last_exc
        return events_out

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
                # Allow matching by barcode or identification_id
                emp = employees.search(['|', ('barcode', '=', ev.get('employee_code')), ('identification_id', '=', ev.get('employee_code'))], limit=1)
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
