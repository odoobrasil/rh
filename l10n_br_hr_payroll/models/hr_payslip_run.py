# -*- coding: utf-8 -*-
# Copyright (C) 2016 KMEE (http://www.kmee.com.br)
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

from datetime import datetime
from dateutil.relativedelta import relativedelta
from pybrasil.data import ultimo_dia_mes
import logging

from openerp import api, fields, models, _
from openerp.exceptions import Warning as UserError
_logger = logging.getLogger(__name__)

MES_DO_ANO = [
    (1, u'Janeiro'),
    (2, u'Fevereiro'),
    (3, u'Março'),
    (4, u'Abril'),
    (5, u'Maio'),
    (6, u'Junho'),
    (7, u'Julho'),
    (8, u'Agosto'),
    (9, u'Setembro'),
    (10, u'Outubro'),
    (11, u'Novembro'),
    (12, u'Dezembro'),
    (13, u'13º Salário')
]

TIPO_DE_FOLHA = [
    ('normal', u'Folha normal'),
    ('adiantamento_13', u'13º Salário - Adiantamento'),
    ('decimo_terceiro', u'13º Salário'),
    ('provisao_ferias', u'Provisão de Férias'),
    ('provisao_decimo_terceiro', u'Provisão de Décimo Terceiro (13º)'),
]


class HrPayslipRun(models.Model):
    _inherit = "hr.payslip.run"
    _order = "ano desc,mes_do_ano desc,tipo_de_folha asc, company_id asc"
    _sql_constraints = [
        ('lote_unico',
         'unique(ano, mes_do_ano, tipo_de_folha, company_id)',
         'Este Lote de Holerite já existe!'),
        ('nome',
         'unique(display_name)',
         'Este nome de Lote já existe ! ' 
         'Por favor digite outro que não se repita')
    ]

    mes_do_ano = fields.Selection(
        selection=MES_DO_ANO,
        string=u'Mês',
        required=True,
        default=datetime.now().month,
    )
    ano = fields.Integer(
        string=u'Ano',
        default=datetime.now().year,
    )
    tipo_de_folha = fields.Selection(
        selection=TIPO_DE_FOLHA,
        string=u'Tipo de folha',
        default='normal',
    )
    contract_id = fields.Many2many(
        comodel_name='hr.contract',
        string='Contratos',
    )
    contract_id_readonly = fields.Many2many(
        comodel_name='hr.contract',
        string='Contratos',
    )
    departamento_id = fields.Many2one(
        comodel_name='hr.department',
        string='Departamento',
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        string='Empresa',
        default=lambda self: self.env.user.company_id or '',
    )

    @api.onchange('tipo_de_folha')
    def fixa_decimo_terceiro(self):
        if self.tipo_de_folha == 'adiantamento_13' and self.mes_do_ano == 12:
            self.tipo_de_folha = 'decimo_terceiro'
            self.mes_do_ano = 13
        else:
            if self.tipo_de_folha == 'decimo_terceiro':
                self.mes_do_ano = 13
            elif self.mes_do_ano == 13:
                self.mes_do_ano = datetime.now().month

    @api.onchange('mes_do_ano', 'ano')
    def buscar_datas_periodo(self):
        if not self.mes_do_ano:
            self.mes_do_ano = datetime.now().month

        if self.tipo_de_folha == 'adiantamento_13' and self.mes_do_ano == 12:
            self.tipo_de_folha = 'decimo_terceiro'
            self.mes_do_ano = 13

        mes = self.mes_do_ano
        if mes > 12:
            mes = 12
            self.tipo_de_folha = 'decimo_terceiro'
        elif self.tipo_de_folha == 'decimo_terceiro':
            self.tipo_de_folha = 'normal'

        ultimo_dia_do_mes = str(
            self.env['resource.calendar'].get_ultimo_dia_mes(
                mes, self.ano))

        primeiro_dia_do_mes = str(
            datetime.strptime(str(mes) + '-' +
                              str(self.ano), '%m-%Y'))

        self.date_start = primeiro_dia_do_mes
        self.date_end = ultimo_dia_do_mes


    @api.multi
    def verificar_holerites_gerados(self):
        for lote in self:
            dominio_contratos = [
                    ('date_start', '<=', lote.date_end),
                    ('company_id', '=', lote.company_id.id),
                ]
            if lote.tipo_de_folha != 'normal':
                dominio_contratos += [
                    ('categoria', 'not in', ['721', '722']),
                ]
            # else:
            #     dominio_contratos += [
            #         ('date_end', '>', lote.date_start),
            #     ]
            contracts_id = self.env['hr.contract'].search(dominio_contratos)

            dominio_payslips = [
                ('tipo_de_folha', '=', self.tipo_de_folha),
                ('contract_id', 'in', contracts_id.ids)
            ]
            if lote.tipo_de_folha != 'provisao_ferias':
                dominio_payslips += [
                    ('date_from', '>=', self.date_start),
                    ('date_to', '<=', self.date_end),
                ]
            else:
                dominio_payslips += [
                    ('mes_do_ano', '=', self.mes_do_ano),
                    ('ano', '=', self.ano),
                ]
            payslips = self.env['hr.payslip'].search(dominio_payslips)

            contratos_com_holerites = []
            for payslip in payslips:
                if payslip.contract_id.id not in contratos_com_holerites:
                    contratos_com_holerites.append(payslip.contract_id.id)

            contratos_sem_holerite = []
            for contrato in contracts_id:
                 if contrato.id not in contratos_com_holerites:
                     if not contrato.date_end:
                         contratos_sem_holerite.append(contrato.id)
                     else:
                         if contrato.date_end > lote.date_end:
                             contratos_sem_holerite.append(contrato.id)

            lote.write({
                'contract_id': [(6, 0, contratos_sem_holerite)],
                'contract_id_readonly': [(6, 0, contratos_sem_holerite)],
            })

    @api.multi
    def gerar_holerites(self):
        self.verificar_holerites_gerados()
        for contrato in self.contract_id:
            if self.tipo_de_folha == 'provisao_ferias':
                inicio_mes = str(self.ano).zfill(4) + '-' + \
                              str(self.mes_do_ano).zfill(2) + '-01'
                if contrato.date_start > inicio_mes:
                    inicio_mes = contrato.date_start
                data_inicio = fields.Date.to_string(ultimo_dia_mes(inicio_mes))
                contrato.action_button_update_controle_ferias(
                    data_referencia=data_inicio)
                for periodo in contrato.vacation_control_ids:
                    if periodo.saldo > 0 and not periodo.inicio_gozo:
                        try:
                            data_fim = fields.Date.from_string(inicio_mes) + \
                                  relativedelta(days=periodo.saldo)
                            payslip_obj = self.env['hr.payslip']
                            payslip = payslip_obj.create({
                                'contract_id': contrato.id,
                                'periodo_aquisitivo': periodo.id,
                                'mes_do_ano': self.mes_do_ano,
                                'mes_do_ano2': self.mes_do_ano,
                                'date_from': inicio_mes,
                                'date_to': data_fim,
                                'ano': self.ano,
                                'employee_id': contrato.employee_id.id,
                                'tipo_de_folha': self.tipo_de_folha,
                                'payslip_run_id': self.id,
                            })
                            # payslip._compute_set_dates()
                            payslip.compute_sheet()
                            self.env.cr.commit()
                            _logger.info(u"Holerite " + contrato.name +
                                         u" processado com sucesso!")
                        except:
                            _logger.warning(u"Holerite " + contrato.name +
                                            u" falhou durante o cálculo!")
                            payslip.unlink()
                            continue
                contrato.action_button_update_controle_ferias()
            else:
                try:
                    tipo_de_folha = self.tipo_de_folha
                    if tipo_de_folha == 'adiantamento_13':
                        tipo_de_folha = 'decimo_terceiro'
                    payslip_obj = self.env['hr.payslip']
                    payslip = payslip_obj.create({
                        'contract_id': contrato.id,
                        'mes_do_ano': self.mes_do_ano,
                        'mes_do_ano2': self.mes_do_ano,
                        'ano': self.ano,
                        'employee_id': contrato.employee_id.id,
                        'tipo_de_folha': tipo_de_folha,
                        'payslip_run_id': self.id,
                    })
                    payslip._compute_set_dates()
                    payslip.compute_sheet()
                    _logger.info(
                        u"Holerite " + contrato.name +
                        u" processado com sucesso!")
                    self.env.cr.commit()
                except:
                    _logger.warning(
                        u"Holerite " + contrato.name +
                        u" falhou durante o cálculo!")
                    payslip.unlink()
                    continue
        self.verificar_holerites_gerados()

    @api.multi
    def close_payslip_run(self):
        for lote in self:
            for holerite in lote.slip_ids:
                holerite.hr_verify_sheet()
        super(HrPayslipRun, self).close_payslip_run()

    @api.multi
    def unlink(self):
        """
        Validacao para exclusao de lote de holerites
        Nao permitir excluir o lote se ao menos um holerite nao estiver no
        state draft.vali
        """
        for lote in self:
            if any(l != 'draft' for l in lote.slip_ids.mapped('state')):
                raise UserError(
                    _('Erro na exclusão deste Lote !\n'
                      'Há holerite(s) já confirmados!')
                )
        return super(HrPayslipRun, self).unlink()
