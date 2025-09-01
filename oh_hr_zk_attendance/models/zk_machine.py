# -*- coding: utf-8 -*-
#############################################################################
#
#    Cybrosys Technologies Pvt. Ltd.
#
#    Copyright (C) 2022-TODAY Cybrosys Technologies(<https://www.cybrosys.com>)
#    Author: Cybrosys Techno Solutions(<https://www.cybrosys.com>)
#
#    You can modify it under the terms of the GNU LESSER
#    GENERAL PUBLIC LICENSE (LGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU LESSER GENERAL PUBLIC LICENSE (LGPL v3) for more details.
#
#    You should have received a copy of the GNU LESSER GENERAL PUBLIC LICENSE
#    (LGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
#############################################################################
import pytz
import sys
import datetime
import logging
import binascii

import requests
from requests.auth import HTTPBasicAuth

from . import zklib
from .zkconst import *
from struct import unpack
from odoo import api, fields, models
from odoo import _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)
try:
    from zk import ZK, const
except ImportError:
    _logger.error("Please Install pyzk library.")

_logger = logging.getLogger(__name__)


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    device_id = fields.Char(string='Biometric Device ID')


class ZkMachine(models.Model):
    _name = 'zk.machine'

    name = fields.Char(string='Machine IP', required=True)
    port_no = fields.Integer(string='Port No', required=True)
    address_id = fields.Many2one('res.partner', string='Working Address')
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.user.company_id.id)

    device_type = fields.Selection([
        ('zk', 'ZKTeco (pyzk)')
        , ('hik', 'Hikvision (ISAPI)')
    ], string='Device Type', required=True, default='zk')
    hik_username = fields.Char(string='Hikvision Username')
    hik_password = fields.Char(string='Hikvision Password')
    use_https = fields.Boolean(string='Use HTTPS', default=False)
    last_fetch_at = fields.Datetime(string='Last Fetch Time')

    def device_connect(self, zk):
        try:
            conn = zk.connect()
            return conn
        except:
            return False

    def clear_attendance(self):
        for info in self:
            if info.device_type == 'hik':
                # Clearing attendance on Hikvision via ISAPI is not recommended
                # and can vary by model/firmware. We avoid destructive operations.
                raise UserError(_("مسح سجلات الحضور من أجهزة Hikvision غير مدعوم من الوحدة حفاظاً على السجلات."))
            try:
                machine_ip = info.name
                zk_port = info.port_no
                timeout = 30
                try:
                    zk = ZK(machine_ip, port=zk_port, timeout=timeout, password=0, force_udp=False, ommit_ping=False)
                except NameError:
                    raise UserError(_("Please install it with 'pip3 install pyzk'."))
                conn = self.device_connect(zk)
                if conn:
                    conn.enable_device()
                    clear_data = zk.get_attendance()
                    if clear_data:
                        # conn.clear_attendance()
                        self._cr.execute("""delete from zk_machine_attendance""")
                        conn.disconnect()
                        raise UserError(_('Attendance Records Deleted.'))
                    else:
                        raise UserError(_('Unable to clear Attendance log. Are you sure attendance log is not empty.'))
                else:
                    raise UserError(
                        _('Unable to connect to Attendance Device. Please use Test Connection button to verify.'))
            except Exception:
                raise ValidationError(
                    'Unable to clear Attendance log. Are you sure attendance device is connected & record is not empty.')

    def getSizeUser(self, zk):
        """Checks a returned packet to see if it returned CMD_PREPARE_DATA,
        indicating that data packets are to be sent

        Returns the amount of bytes that are going to be sent"""
        command = unpack('HHHH', zk.data_recv[:8])[0]
        if command == CMD_PREPARE_DATA:
            size = unpack('I', zk.data_recv[8:12])[0]
            return size
        else:
            return False

    def zkgetuser(self, zk):
        """Start a connection with the time clock"""
        try:
            users = zk.get_users()
            return users
        except:
            return False

    @api.model
    def cron_download(self):
        machines = self.env['zk.machine'].search([])
        for machine in machines:
            machine.download_attendance()

    def _hik_base_url(self, info):
        scheme = 'https' if info.use_https else 'http'
        return f"{scheme}://{info.name}:{info.port_no}"

    def _hik_fetch_events(self, info, start_dt, end_dt, max_results=200):
        """Fetch events from Hikvision device via ISAPI.
        start_dt, end_dt: aware/naive datetimes (assumed UTC if naive)
        Returns list of event dicts or raises UserError
        """
        base = self._hik_base_url(info)
        url = f"{base}/ISAPI/AccessControl/AcsEvent?format=json"
        # Format times ISO8601 with timezone +00:00
        def to_iso(dt):
            if isinstance(dt, str):
                return dt
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            return dt.isoformat()
        payload = {
            "AcsEventCond": {
                "searchID": "odoo-1",
                "searchResultPosition": 0,
                "maxResults": max_results,
                "major": 0,
                # Leave minor unspecified to include all; some firmwares reject arrays.
                "startTime": to_iso(start_dt),
                "endTime": to_iso(end_dt)
            }
        }
        auth = None
        if info.hik_username and info.hik_password:
            auth = HTTPBasicAuth(info.hik_username, info.hik_password)
        try:
            resp = requests.post(url, json=payload, auth=auth, timeout=20, verify=False)
        except Exception as e:
            raise UserError(_(f"تعذر الاتصال بجهاز Hikvision: {e}"))
        if resp.status_code == 401:
            raise UserError(_("بيانات الدخول إلى جهاز Hikvision غير صحيحة (401)."))
        if resp.status_code >= 400:
            raise UserError(_(f"فشل طلب ISAPI ({resp.status_code}): {resp.text[:200]}"))
        try:
            data = resp.json()
        except Exception:
            raise UserError(_("استجابة غير صالحة من جهاز Hikvision (JSON)."))
        # Normalize to list
        events = []
        if isinstance(data, dict):
            # Common keys: 'AcsEvent', 'AcsEventArray', or 'Event'
            for key in ('AcsEvent', 'AcsEventArray', 'Event'):
                if key in data and isinstance(data[key], list):
                    events = data[key]
                    break
            if not events and 'AcsEvent' in data and isinstance(data['AcsEvent'], dict):
                events = [data['AcsEvent']]
        elif isinstance(data, list):
            events = data
        return events

    def _hik_process_events(self, info, events):
        zk_attendance = self.env['zk.machine.attendance']
        att_obj = self.env['hr.attendance']

        def parse_time(ts):
            # Expect formats like '2023-08-22T12:34:56+08:00' or '2023-08-22T12:34:56Z'
            try:
                ts2 = ts.replace('Z', '+00:00')
                dt_obj = datetime.datetime.fromisoformat(ts2)
            except Exception:
                try:
                    dt_obj = datetime.datetime.strptime(ts[:19], '%Y-%m-%dT%H:%M:%S')
                except Exception:
                    _logger.warning("HIK: Unable to parse time %s", ts)
                    return None
            # Convert to UTC then to string Odoo format
            if dt_obj.tzinfo is None:
                dt_obj = dt_obj.replace(tzinfo=pytz.UTC)
            utc_dt = dt_obj.astimezone(pytz.UTC)
            return fields.Datetime.to_string(utc_dt)

        for ev in events:
            ts = ev.get('time') or ev.get('Time') or ev.get('timeStr') or ev.get('eventTime')
            if not ts:
                continue
            atten_time = parse_time(ts)
            if not atten_time:
                continue
            # Identify employee by employeeNoString or cardNo
            dev_id = ev.get('employeeNoString') or ev.get('employeeNo') or ev.get('cardNo') or ev.get('cardNumber')
            if dev_id is None:
                # Some events may carry personId
                dev_id = ev.get('personId') or ev.get('userId')
            if dev_id is None:
                continue
            dev_id = str(dev_id)

            # Determine attendance_type
            minor = ev.get('minor')
            attendance_type = '4'  # default Card
            try:
                minor_int = int(minor) if minor is not None else None
                if minor_int in (75, 76, 77, 78):  # face related (approx)
                    attendance_type = '15'
            except Exception:
                pass

            # Upsert attendance line and hr.attendance check-in/out heuristic
            get_user_id = self.env['hr.employee'].search([('device_id', '=', dev_id)], limit=1)
            if not get_user_id:
                # Optionally create employee placeholder with name from event
                emp_name = ev.get('name') or f"Device User {dev_id}"
                get_user_id = self.env['hr.employee'].create({'device_id': dev_id, 'name': emp_name})
                # Create a check-in record directly
                att_obj.create({'employee_id': get_user_id.id, 'check_in': atten_time})

            # Skip duplicate
            duplicate = zk_attendance.search([
                ('device_id', '=', dev_id),
                ('punching_time', '=', atten_time)
            ], limit=1)
            if duplicate:
                continue

            # Heuristic punch type: open check-in without checkout -> checkout, else check-in
            att_open = att_obj.search([('employee_id', '=', get_user_id.id), ('check_out', '=', False)], limit=1)
            punch_type = '1' if att_open else '0'

            zk_attendance.create({
                'employee_id': get_user_id.id,
                'device_id': dev_id,
                'attendance_type': attendance_type,
                'punch_type': punch_type,
                'punching_time': atten_time,
                'address_id': info.address_id.id
            })

            if punch_type == '0':
                if not att_open:
                    att_obj.create({'employee_id': get_user_id.id, 'check_in': atten_time})
            else:
                if att_open:
                    att_open.write({'check_out': atten_time})
                else:
                    # if no open, attach to last attendance
                    last_att = att_obj.search([('employee_id', '=', get_user_id.id)], order='check_in desc', limit=1)
                    if last_att:
                        last_att.write({'check_out': atten_time})

    def download_attendance(self):
        _logger.info("++++++++++++Cron Executed++++++++++++++++++++++")
        zk_attendance = self.env['zk.machine.attendance']
        att_obj = self.env['hr.attendance']
        for info in self:
            if info.device_type == 'hik':
                start_dt = info.last_fetch_at or (fields.Datetime.now() and (fields.Datetime.from_string(fields.Datetime.now()) - datetime.timedelta(days=1)))
                end_dt = fields.Datetime.now()
                try:
                    events = self._hik_fetch_events(info, start_dt, end_dt)
                except UserError as e:
                    raise e
                except Exception as e:
                    raise UserError(_(f"حدث خطأ أثناء جلب سجلات Hikvision: {e}"))
                if not events:
                    raise UserError(_("لا توجد سجلات حضور جديدة على جهاز Hikvision."))
                self._hik_process_events(info, events)
                info.last_fetch_at = end_dt
                return True

            # Default: ZKTeco path (existing)
            machine_ip = info.name
            zk_port = info.port_no
            timeout = 15
            try:
                zk = ZK(machine_ip, port=zk_port, timeout=timeout, password=0, force_udp=False, ommit_ping=False)
            except NameError:
                raise UserError(_("Pyzk module not Found. Please install it with 'pip3 install pyzk'."))
            conn = self.device_connect(zk)
            if conn:
                # conn.disable_device() #Device Cannot be used during this time.
                try:
                    user = conn.get_users()
                except Exception:
                    user = False
                try:
                    attendance = conn.get_attendance()
                except Exception:
                    attendance = False
                if attendance:
                    for each in attendance:
                        atten_time = each.timestamp
                        atten_time = datetime.datetime.strptime(atten_time.strftime('%Y-%m-%d %H:%M:%S'), '%Y-%m-%d %H:%M:%S')
                        local_tz = pytz.timezone(
                            self.env.user.partner_id.tz or 'GMT')
                        local_dt = local_tz.localize(atten_time, is_dst=None)
                        utc_dt = local_dt.astimezone(pytz.utc)
                        utc_dt = utc_dt.strftime("%Y-%m-%d %H:%M:%S")
                        atten_time = datetime.datetime.strptime(
                            utc_dt, "%Y-%m-%d %H:%M:%S")
                        atten_time = fields.Datetime.to_string(atten_time)
                        if user:
                            for uid in user:
                                if uid.user_id == each.user_id:
                                    get_user_id = self.env['hr.employee'].search(
                                        [('device_id', '=', each.user_id)])
                                    if get_user_id:
                                        duplicate_atten_ids = zk_attendance.search(
                                            [('device_id', '=', each.user_id), ('punching_time', '=', atten_time)])
                                        if duplicate_atten_ids:
                                            continue
                                        else:
                                            zk_attendance.create({'employee_id': get_user_id.id,
                                                                  'device_id': each.user_id,
                                                                  'attendance_type': str(each.status),
                                                                  'punch_type': str(each.punch),
                                                                  'punching_time': atten_time,
                                                                  'address_id': info.address_id.id})
                                            att_var = att_obj.search([('employee_id', '=', get_user_id.id),
                                                                      ('check_out', '=', False)])
                                            if each.punch == 0:  # check-in
                                                if not att_var:
                                                    att_obj.create({'employee_id': get_user_id.id,
                                                                    'check_in': atten_time})
                                            if each.punch == 1:  # check-out
                                                if len(att_var) == 1:
                                                    att_var.write({'check_out': atten_time})
                                                else:
                                                    att_var1 = att_obj.search([('employee_id', '=', get_user_id.id)])
                                                    if att_var1:
                                                        att_var1[-1].write({'check_out': atten_time})

                                    else:
                                        employee = self.env['hr.employee'].create(
                                            {'device_id': each.user_id, 'name': uid.name})
                                        zk_attendance.create({'employee_id': employee.id,
                                                              'device_id': each.user_id,
                                                              'attendance_type': str(each.status),
                                                              'punch_type': str(each.punch),
                                                              'punching_time': atten_time,
                                                              'address_id': info.address_id.id})
                                        att_obj.create({'employee_id': employee.id,
                                                        'check_in': atten_time})
                                else:
                                    pass
                    # zk.enableDevice()
                    conn.disconnect
                    return True
                else:
                    raise UserError(_('Unable to get the attendance log, please try again later.'))
            else:
                raise UserError(_('Unable to connect, please check the parameters and network connections.'))
