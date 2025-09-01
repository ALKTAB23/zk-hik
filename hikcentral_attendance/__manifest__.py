# -*- coding: utf-8 -*-
{
    'name': 'HikCentral Attendance Integration',
    'version': '17.0.1.0.0',
    'summary': 'Import attendance logs from HikCentral by date range',
    'description': 'Pulls attendance events from HikCentral and creates hr.attendance records with date-range filters and cron sync.',
    'author': 'Your Company',
    'website': 'https://example.com',
    'license': 'LGPL-3',
    'depends': ['hr', 'hr_attendance'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/hikcentral_views.xml',
        'wizard/hikcentral_import_wizard_views.xml',
        'data/cron.xml',
    ],
    'installable': True,
    'application': False,
}