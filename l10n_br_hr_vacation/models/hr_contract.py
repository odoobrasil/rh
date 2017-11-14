# -*- coding: utf-8 -*-
# Copyright 2016 KMEE - Hendrix Costa <hendrix.costa@kmee.com.br>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from openerp import api, models, fields, _
from dateutil.relativedelta import relativedelta
from lxml import etree
from openerp.exceptions import Warning as UserError

try:
    from pybrasil import data

except ImportError:
    _logger.info('Cannot import pybrasil')

class HrContract(models.Model):
    _inherit = 'hr.contract'

    vacation_control_ids = fields.One2many(
        comodel_name='hr.vacation.control',
        inverse_name='contract_id',
        string=u'Periodos Aquisitivos Alocados',
        ondelete="cascade",
    )

    def create_controle_ferias(self, inicio_periodo_aquisitivo):
        fim_aquisitivo = fields.Date.from_string(inicio_periodo_aquisitivo) + \
            relativedelta(years=1, days=-1)

        inicio_concessivo = fim_aquisitivo + relativedelta(days=1)
        fim_concessivo = inicio_concessivo + \
            relativedelta(years=1, days=-1)

        limite_gozo = fim_concessivo + relativedelta(months=-1)
        limite_aviso = limite_gozo + relativedelta(months=-1)

        controle_ferias = self.env['hr.vacation.control'].create({
            'inicio_aquisitivo': inicio_periodo_aquisitivo,
            'fim_aquisitivo': fim_aquisitivo,
            'inicio_concessivo': inicio_concessivo,
            'fim_concessivo': fim_concessivo,
            'limite_gozo': limite_gozo,
            'limite_aviso': limite_aviso,
        })
        return controle_ferias

    @api.multi
    def write(self, vals):
        """
        No HrContract, o método write é chamado tanto na api antiga quanto na
        api nova. No caso da alteração da data de início do contrato, a chamada
        é feita na api antiga, dessa forma, é passado uma lista com os ids a
        serem escritos e os valores a serem alterados num dicionário chamado
        context. A programação abaixo foi feita da seguinte forma: primeiro
        verifica-se se há holidays do tipo remove atrelados ao contrato, pois
        se existe, a data de início do contrato não pode ser alterado por
        motivos de integridade do sistema. Depois são deletados os holidays do
        tipo add e as linhas de controle de férias antigas. Por último, são
        criadas novas linhas de controle de férias e holidays do tipo add para
        a nova data de início do contrato.
        """
        if vals.get('date_start') and \
                (vals.get('date_start') != self.date_start):
            self.verificar_controle_ferias()
            self.atualizar_linhas_controle_ferias(vals.get('date_start'))
        contract_id = super(HrContract, self).write(vals)
        # se o contrato ja se encerrou, replicar no controle de férias
        if 'date_end' in vals:
            self.atualizar_data_demissao()
        return contract_id

    @api.model
    def create(self, vals):
        hr_contract_id = super(HrContract, self).create(vals)
        if vals.get('date_start'):
            hr_contract_id.atualizar_linhas_controle_ferias(
                vals.get('date_start'))
        # se o contrato ja se encerrou, replicar no controle de férias
        if vals.get('date_end'):
            hr_contract_id.atualizar_data_demissao()
        return hr_contract_id

    def atualizar_data_demissao(self):
        """
        Se o contrato ja foi encerrado, replica a informação para o
        controle de ferias computar corretamente as ferias de direito
        :return:
        """
        if self.date_end and \
                self.vacation_control_ids and \
                self.vacation_control_ids[0].fim_aquisitivo > self.date_end:
            self.vacation_control_ids[0].fim_aquisitivo = self.date_end
            self.vacation_control_ids[0].inicio_concessivo = ''
            self.vacation_control_ids[0].fim_concessivo = ''

        # Se estiver reativando o contrato, isto é, removendo a data de demiss
        if not self.date_end:
            vc_obj = self.vacation_control_ids
            inicio_aquisit = self.vacation_control_ids[0].inicio_aquisitivo
            vals = vc_obj.calcular_datas_aquisitivo_concessivo(inicio_aquisit)
            # Atualizar datas do ultimo controle de ferias
            ultimo_controle = self.vacation_control_ids[0]
            ultimo_controle.fim_aquisitivo = vals.get('fim_aquisitivo')
            ultimo_controle.inicio_concessivo = vals.get('inicio_concessivo')
            ultimo_controle.fim_concessivo = vals.get('fim_concessivo')

    @api.multi
    def action_button_update_controle_ferias(self):
        """
        Ação disparada pelo botão na view, que atualiza as linhas de controle
        de férias
        """
        #self.verificar_controle_ferias()
        #self.atualizar_linhas_controle_ferias(self.date_start)

        for contrato in self:

            # Apagar todos os períodos aquisitivos do contrato
            #
            for periodo_aquisitivo in contrato.vacation_control_ids:
                periodo_aquisitivo.unlink()

            # Criar os períodos aquisitivos
            #
            inicio = fields.Date.from_string(contrato.date_start)
            hoje = fields.Date.from_string(fields.Date.today())
            lista_controle_ferias = []
            controle_ferias_obj = self.env['hr.vacation.control']

            while inicio <= hoje:
                vals = \
                    controle_ferias_obj.calcular_datas_aquisitivo_concessivo(
                        str(inicio)
                    )
                controle_ferias = controle_ferias_obj.create(vals)
                inicio = inicio + relativedelta(years=1)
                lista_controle_ferias.append(controle_ferias.id)

            # Ordena períodos aquisitivos recalculados
            #
            contrato.vacation_control_ids = sorted(lista_controle_ferias,
                                               reverse=True)

            # Buscar Férias registradas e atualizar os períodos aquisitivos
            #
            domain = [
                ('contract_id', '=', contrato.id),
                ('tipo_de_folha', '=', 'ferias'),
                ('is_simulacao', '=', False),
                ('state', 'in', ['done', 'verify']),
            ]
            holerites_ids = \
                self.env['hr.payslip'].search(domain, order='date_from')

            for holerite in holerites_ids:
                for periodo in contrato.vacation_control_ids:

                    # Busca o aviso de férias do mesmo período aquisitivo
                    if periodo.inicio_aquisitivo == holerite.inicio_aquisitivo\
                            and not periodo.inicio_gozo:

                        # Calcula dias gozados
                        #
                        data_inicio = \
                            fields.Date.from_string(holerite.date_from)
                        data_fim = fields.Date.from_string(holerite.date_to)
                        abono_pecuniario = \
                            holerite.holidays_ferias.sold_vacations_days
                        dias_gozados = (data_fim - data_inicio).days + 1 + \
                                       abono_pecuniario

                        # Cria novo período aquisitivo se ainda houver saldo
                        #
                        if (periodo.saldo - dias_gozados) > 0:
                            novo_periodo = periodo.copy()
                            novo_periodo.dias_gozados_anteriormente = \
                                dias_gozados + \
                                periodo.dias_gozados_anteriormente

                        # Altera os valores do período aquisitivo com os dados
                        # do aviso de férias
                        #
                        periodo.inicio_gozo = holerite.date_from
                        periodo.fim_gozo = holerite.date_to
                        periodo.data_aviso = holerite.date_from
                        periodo.dias_gozados = dias_gozados
                        holerite.periodo_aquisitivo = periodo
                        domain = [
                            ('data_inicio', '=', periodo.inicio_gozo),
                            ('data_fim', '=', periodo.fim_gozo)
                        ]
                        holidays = self.env['hr.holidays'].search(domain)
                        if holidays:
                            holidays.controle_ferias = [(4, periodo.id)]

            # Atualizar último periodo aquisitivo caso a data de demissão
            # esteja definida
            #
            self.atualizar_data_demissao()

            # self.atualizar_linhas_controle_ferias(self.date_start)

    @api.model
    def fields_view_get(self, view_id=None, view_type='form',
                        toolbar=False, submenu=False):
        res = super(HrContract, self).fields_view_get(
            view_id=view_id, view_type=view_type, toolbar=toolbar,
            submenu=submenu
        )
        if view_type == 'form':
            doc = etree.XML(res['arch'])
            for sheet in doc.xpath("//sheet"):
                parent = sheet.getparent()
                index = parent.index(sheet)
                for child in sheet:
                    parent.insert(index, child)
                    index += 1
                parent.remove(sheet)
            res['arch'] = etree.tostring(doc)
        return res

    @api.multi
    def atualizar_controle_ferias(self):
        """
        Função disparada  pelo cron que dispara diarimente.
        Atualiza o controle de férias, verificando por periodos
        aquisitivos que se encerraram ontem, para criar novas linhas de
        controle de ferias.
        """
        domain = [
            '|',
            ('date_end', '>', fields.Date.today()),
            ('date_end', '=', False),
        ]
        contratos_ids = self.env['hr.contract'].search(domain)

        for contrato in contratos_ids:
            # Se o contrato estiver encerrado nao atualizar
            if contrato.date_end:
                contrato.atualizar_data_demissao()
                continue
            if contrato.vacation_control_ids:
                ultimo_controles = contrato.vacation_control_ids[0]
                for ultimo_controle in ultimo_controles:
                    if ultimo_controle.fim_aquisitivo < fields.Date.today():
                        controle_ferias_obj = self.env['hr.vacation.control']
                        hoje = fields.Date.today()
                        vals = controle_ferias_obj.\
                            calcular_datas_aquisitivo_concessivo(hoje)
                        novo_controle_ferias = controle_ferias_obj.create(vals)
                        novo_controle_ferias.gerar_holidays_ferias()
                        novo_controle_ferias.contract_id = contrato

                programacao_ferias = self.env['ir.config_parameter'].get_param(
                    'l10n_br_hr_vacation_programacao_ferias_futuras',
                    default=False
                )

                if programacao_ferias:
                    dias = ultimo_controle.dias
                else:
                    dias = ultimo_controle.saldo

                for periodo_aquisitivo in ultimo_controle.hr_holiday_ids:
                    if periodo_aquisitivo.type == 'add':
                        periodo_aquisitivo.number_of_days_temp = dias
