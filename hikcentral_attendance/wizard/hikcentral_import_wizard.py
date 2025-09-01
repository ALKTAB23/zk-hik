# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class HikImportWizard(models.TransientModel):
    _name = 'hik.import.wizard'
    _description = 'Import Attendance from HikCentral (Date Range)'

    date_from = fields.Datetime(required=True)
    date_to = fields.Datetime(required=True)

    def action_import(self):
        if self.date_to < self.date_from:
            raise UserError(_('End date must be after start date'))
        service = self.env['hikcentral.service']
        log = service.import_range(self.date_from, self.date_to)
        action = self.env.ref('hikcentral_attendance.action_hik_import_logs').read()[0]
        return action
