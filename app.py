import os
import io
import re
import base64
import traceback
import json
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, abort, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from docx import Document

from google import genai 
from google.genai import types # IMPORT VITAL: Google Grounding (Pesquisa ao vivo)
import openai 

app = Flask(__name__)

# =========================================================
# TELA DE RAIO-X (Tratamento de Erros)
# =========================================================
@app.errorhandler(Exception)
def handle_exception(e):
    try: 
        db.session.rollback()
    except: 
        pass
    
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException): 
        return e
        
    return f"""
    <div style="font-family: sans-serif; padding: 20px; background: #262730; color: #E1E4E8; height: 100vh;">
        <h1 style="color: #FF4B4B;">🚨 Falha Crítica Detectada</h1>
        <p>O sistema encontrou um erro interno.</p>
        <div style="background: #1A1C23; padding: 15px; border-radius: 5px; color: #FFC107; font-family: monospace; overflow-x: auto;">
            <b>{type(e).__name__}</b>: {str(e)}
        </div>
        <p style="margin-top: 20px;">Tire um print e envie para análise.</p>
        <a href="/" style="color: #2196F3; text-decoration: none;">⬅️ Tentar Voltar</a>
    </div>
    """, 500

# =========================================================
# CONFIGURAÇÕES DO BANCO DE DADOS E SEGURANÇA
# =========================================================
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chave-super-secreta-mude-depois')

# Proteção para URLs do Neon antigas
db_url = os.environ.get('DATABASE_URL', 'sqlite:///clientes.db')
if db_url.startswith("postgres://"): 
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True, 
    "pool_recycle": 60, 
    "pool_timeout": 30
}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "Por favor, faça login para acessar."

CHAVE_API_GOOGLE = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=CHAVE_API_GOOGLE) if CHAVE_API_GOOGLE else None

CHAVE_OPENROUTER = os.environ.get("OPENAI_API_KEY")

# =========================================================
# MODELOS DO BANCO DE DADOS
# =========================================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='admin') 
    expiration_date = db.Column(db.Date, nullable=True)
    alunos = db.relationship('Aluno', backref='responsavel', lazy=True)

class Aluno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    curso = db.Column(db.String(100))
    telefone = db.Column(db.String(20))
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='Produção') 
    valor = db.Column(db.Float, default=70.0) 
    documentos = db.relationship('Documento', backref='aluno', lazy=True, cascade="all, delete-orphan")

class Documento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    dados_arquivo = db.Column(db.LargeBinary, nullable=False) 
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)

class PromptConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    texto = db.Column(db.Text, nullable=False)
    is_default = db.Column(db.Boolean, default=False)

class RegistroUso(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    modelo_usado = db.Column(db.String(100))

# NOVO MODELO: Configurações Globais do Site
class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    whatsapp_template = db.Column(db.Text, default="Olá {nome}, seu trabalho de {curso} ficou pronto com excelência! 🎉\nO valor acordado foi R$ {valor}.\n\nSegue a minha chave PIX para liberação do arquivo: [SUA CHAVE AQUI]")

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

PROMPT_REGRAS_BASE = """
    REGRA DE OURO (LINGUAGEM HUMANA E LIMITES RIGOROSOS):
    - PROIBIDO usar palavras robóticas de IA.
    - Escreva de forma natural e acadêmica.
    - NÃO USE FORMATO JSON.
    - OBRIGATÓRIO (LIMITE DE PARÁGRAFOS): 
      * Resumo: EXATAMENTE 1 parágrafo.
      * Contexto: EXATAMENTE 1 parágrafo.
      * Análise: EXATAMENTE 1 parágrafo.
      * Propostas de solução: MÁXIMO de 2 parágrafos.
      * Conclusão reflexiva: MÁXIMO de 2 parágrafos.
      * Autoavaliação: EXATAMENTE 1 parágrafo (sem atribuir nota a si mesmo).
    
    GERAÇÃO OBRIGATÓRIA:
    [START_RESUMO_MEMORIAL] [Resposta] [END_RESUMO_MEMORIAL]
    [START_CONTEXTO_MEMORIAL] [Resposta] [END_CONTEXTO_MEMORIAL]
    [START_ANALISE_MEMORIAL] [Resposta] [END_ANALISE_MEMORIAL]
    [START_ASPECTO_1] [Resposta] [END_ASPECTO_1]
    [START_POR_QUE_1] [Resposta] [END_POR_QUE_1]
    [START_ASPECTO_2] [Resposta] [END_ASPECTO_2]
    [START_POR_QUE_2] [Resposta] [END_POR_QUE_2]
    [START_ASPECTO_3] [Resposta] [END_ASPECTO_3]
    [START_POR_QUE_3] [Resposta] [END_POR_QUE_3]
    [START_CONCEITOS_TEORICOS] [Resposta] [END_CONCEITOS_TEORICOS]
    [START_ANALISE_CONCEITO_1] [Resposta] [END_ANALISE_CONCEITO_1]
    [START_ENTENDIMENTO_TEORICO] [Resposta] [END_ENTENDIMENTO_TEORICO]
    [START_SOLUCOES_TEORICAS] [Resposta] [END_SOLUCOES_TEORICAS]
    [START_PROPOSTAS_MEMORIAL] [Resposta] [END_PROPOSTAS_MEMORIAL]
    [START_CONCLUSAO_MEMORIAL] [Resposta] [END_CONCLUSAO_MEMORIAL]
    [START_REFERENCIAS_ADICIONAIS] [Referências ABNT diretas (Uso da Internet Permitido)] [END_REFERENCIAS_ADICIONAIS]
    [START_AUTOAVALIACAO_MEMORIAL] [Resposta] [END_AUTOAVALIACAO_MEMORIAL]
"""

# Inicialização Blindada
with app.app_context():
    db.create_all()
    
    try: 
        db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN status VARCHAR(20) DEFAULT 'Produção'"))
        db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try: 
        db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN valor FLOAT DEFAULT 70.0"))
        db.session.commit()
    except Exception: 
        db.session.rollback()

    try:
        if not User.query.filter_by(username='admin').first():
            hashed_pw = generate_password_hash('admin123')
            admin = User(username='admin', password=hashed_pw, role='admin')
            db.session.add(admin)
            db.session.commit()
    except Exception: 
        db.session.rollback()
        
    try:
        if not PromptConfig.query.first():
            p = PromptConfig(nome="Padrão Oficial (Desafio UNIASSELVI)", texto=PROMPT_REGRAS_BASE, is_default=True)
            db.session.add(p)
            db.session.commit()
    except Exception: 
        db.session.rollback()

    try:
        if not SiteSettings.query.first():
            db.session.add(SiteSettings())
            db.session.commit()
    except Exception: 
        db.session.rollback()

# =========================================================
# FUNÇÕES DA IA E WORD (COM GOOGLE GROUNDING ATIVADO)
# =========================================================
def limpar_texto_ia(texto):
    try: 
        texto = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), texto)
    except: 
        pass
    return texto

def chamar_ia(prompt, nome_modelo):
    if "openrouter" in nome_modelo.lower() or "/" in nome_modelo:
        if not CHAVE_OPENROUTER: 
            raise Exception("A Chave da API do OpenRouter não foi configurada.")
        
        or_client = openai.OpenAI(
            api_key=CHAVE_OPENROUTER, 
            base_url="https://openrouter.ai/api/v1"
        )
        response = or_client.chat.completions.create(
            model=nome_modelo,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        return limpar_texto_ia(response.choices[0].message.content)
    else:
        if not client: 
            raise Exception("A Chave da API do Google não foi configurada.")
            
        # Tenta aceder à Internet em tempo real para as referências (Google Grounding)
        try:
            resposta = client.models.generate_content(
                model=nome_modelo, 
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
            )
        except Exception:
            # Fallback de segurança se o modelo bloquear a pesquisa web
            resposta = client.models.generate_content(
                model=nome_modelo, 
                contents=prompt
            )
            
        return limpar_texto_ia(resposta.text)

def preencher_template_com_tags(arquivo_template, dicionario_dados):
    doc = Document(arquivo_template)
    def processar_paragrafo(paragrafo):
        texto_original = paragrafo.text
        tem_tag = False
        for marcador, texto_novo in dicionario_dados.items():
            if marcador in texto_original:
                texto_original = texto_original.replace(marcador, str(texto_novo))
                tem_tag = True
        if tem_tag:
            titulos_memorial = ["Resumo", "Contextualização do desafio", "Análise", "Propostas de solução", "Conclusão reflexiva", "Referências", "Autoavaliação"]
            for t in titulos_memorial:
                if texto_original.strip().startswith(t):
                    texto_original = texto_original.replace(t, f"**{t}**\n", 1)
            titulos_aspectos = ["Aspecto 1:", "Aspecto 2:", "Aspecto 3:", "Por quê:"]
            for t in titulos_aspectos:
                if t in texto_original:
                    texto_original = texto_original.replace(t, f"\n**{t}** " if "Por quê:" in t else f"**{t}** ")
            if "?" in texto_original and "Por quê:" not in texto_original:
                partes = texto_original.split("?", 1)
                pergunta = partes[0].strip()
                if 10 < len(pergunta) < 150 and not pergunta.startswith("**"):
                    texto_original = f"**{pergunta}?**\n" + partes[1].lstrip()
            texto_original = texto_original.replace("**\n ", "**\n").replace(":**\n:", ":**\n")
            paragrafo.clear()
            linhas = texto_original.split('\n')
            for i, linha in enumerate(linhas):
                partes = linha.split('**')
                for j, parte in enumerate(partes):
                    if parte: 
                        run = paragrafo.add_run(parte)
                        if j % 2 == 1: run.bold = True
                if i < len(linhas) - 1: paragrafo.add_run('\n')

    for paragrafo in doc.paragraphs: processar_paragrafo(paragrafo)
    for tabela in doc.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for paragrafo in celula.paragraphs: processar_paragrafo(paragrafo)

    arquivo_saida = io.BytesIO()
    doc.save(arquivo_saida)
    arquivo_saida.seek(0)
    return arquivo_saida

def extrair_texto_docx(arquivo_bytes):
    doc = Document(arquivo_bytes)
    return "\n".join([p.text for p in doc.paragraphs])

def extrair_dicionario(texto_ia):
    chaves = ["ASPECTO_1", "POR_QUE_1", "ASPECTO_2", "POR_QUE_2", "ASPECTO_3", "POR_QUE_3", "CONCEITOS_TEORICOS", "ANALISE_CONCEITO_1", "ENTENDIMENTO_TEORICO", "SOLUCOES_TEORICAS", "RESUMO_MEMORIAL", "CONTEXTO_MEMORIAL", "ANALISE_MEMORIAL", "PROPOSTAS_MEMORIAL", "CONCLUSAO_MEMORIAL", "REFERENCIAS_ADICIONAIS", "AUTOAVALIACAO_MEMORIAL"]
    dic = {}
    for chave in chaves:
        match = re.search(rf"\[START_{chave}\](.*?)\[END_{chave}\]", texto_ia, re.DOTALL)
        dic[f"{{{{{chave}}}}}"] = match.group(1).strip() if match else "" 
    return dic

def gerar_correcao_ia_tags(texto_tema, texto_trabalho, critica, nome_modelo):
    config = PromptConfig.query.filter_by(is_default=True).first()
    regras = config.texto if config else PROMPT_REGRAS_BASE
    
    prompt = f"""Você é um professor avaliador extremamente rigoroso.
    TEMA: {texto_tema}
    TRABALHO ATUAL: {texto_trabalho}
    CRÍTICA RECEBIDA: {critica}
    
    TAREFA: Reescreva as respostas aplicando as melhorias exigidas na crítica. 
    Lembre-se: Respeite rigorosamente o limite de caracteres e os limites exatos de parágrafos.
    {regras}"""
    
    try:
        texto_resposta = chamar_ia(prompt, nome_modelo)
        return extrair_dicionario(texto_resposta)
    except Exception as e:
        raise Exception(f"Falha na IA (Correção): {str(e)}")

# =========================================================
# ROTAS PÚBLICAS (PORTAL DO ALUNO)
# =========================================================
@app.route('/portal', methods=['GET', 'POST'])
def portal():
    aluno = None
    erro = None
    if request.method == 'POST':
        telefone_busca = request.form.get('telefone')
        # Filtra para procurar apenas pelos números digitados
        tel_limpo = re.sub(r'\D', '', telefone_busca)
        
        todos_alunos = Aluno.query.all()
        for a in todos_alunos:
            if re.sub(r'\D', '', a.telefone) == tel_limpo:
                aluno = a
                break
                
        if not aluno:
            erro = "Nenhum trabalho encontrado para este número de WhatsApp."
            
    return render_template('portal.html', aluno=aluno, erro=erro)

# =========================================================
# ROTAS DO ADMIN (GERADOR E DASHBOARD)
# =========================================================

MODELOS_DISPONIVEIS = [
    "gemini-2.5-flash",                             
    "gemini-2.5-pro",                               
    "gemini-2.5-flash-lite",                        
    "openrouter/auto"                    
]

@app.route('/')
@login_required
def index():
    if current_user.role == 'cliente' and current_user.expiration_date and date.today() > current_user.expiration_date:
        return render_template('expirado.html')
        
    # Oculta os alunos pagos da lista de geração para manter limpo
    alunos_ativos = Aluno.query.filter(Aluno.user_id==current_user.id, Aluno.status != 'Pago').all()
    prompts = PromptConfig.query.all()
    
    return render_template('index.html', modelos=MODELOS_DISPONIVEIS, alunos=alunos_ativos, prompts=prompts)

@app.route('/gerar_rascunho', methods=['POST'])
@login_required
def gerar_rascunho():
    tema = request.form.get('tema')
    modelo = request.form.get('modelo')
    prompt_id = request.form.get('prompt_id')
    
    config = PromptConfig.query.get(prompt_id) if prompt_id else PromptConfig.query.filter_by(is_default=True).first()
    texto_prompt = config.texto if config else PROMPT_REGRAS_BASE
    prompt_completo = f"TEMA/CASO DO DESAFIO:\n{tema}\n\n{texto_prompt}"

    try:
        texto_resposta = chamar_ia(prompt_completo, modelo)
        dicionario = extrair_dicionario(texto_resposta)
        
        if not any(dicionario.values()):
            raise Exception("A IA não retornou o formato de tags esperado.")
            
        db.session.add(RegistroUso(modelo_usado=modelo))
        db.session.commit()
        
        return jsonify({"sucesso": True, "dicionario": dicionario})
        
    except Exception as e:
        # Sistema de Fallback
        try: 
            next_model = MODELOS_DISPONIVEIS[(MODELOS_DISPONIVEIS.index(modelo) + 1) % len(MODELOS_DISPONIVEIS)]
        except: 
            next_model = MODELOS_DISPONIVEIS[0]
            
        return jsonify({
            "sucesso": False, "erro": str(e), 
            "fallback": True, "failed_model": modelo, "suggested_model": next_model
        })

@app.route('/gerar_docx_final', methods=['POST'])
@login_required
def gerar_docx_final():
    try:
        dados = request.json
        dicionario_editado = dados.get('dicionario')
        aluno_id = dados.get('aluno_id')
        
        caminho_padrao = os.path.join(app.root_path, 'TEMPLATE_COM_TAGS.docx')
        with open(caminho_padrao, 'rb') as f: arquivo_memoria = io.BytesIO(f.read())
        
        documento_pronto = preencher_template_com_tags(arquivo_memoria, dicionario_editado)
        arquivo_bytes = documento_pronto.read()
        
        if aluno_id:
            novo_doc = Documento(aluno_id=aluno_id, nome_arquivo=f"Trabalho_{datetime.now().strftime('%d%m%Y')}.docx", dados_arquivo=arquivo_bytes)
            db.session.add(novo_doc)
            
            aluno = Aluno.query.get(aluno_id)
            if aluno and aluno.status == 'Produção': 
                aluno.status = 'Pendente' # Move automaticamente no Kanban para "Aguardando Pagamento"
                
            db.session.commit()
            
        return jsonify({"sucesso": True, "arquivo_base64": base64.b64encode(arquivo_bytes).decode('utf-8')})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/dashboard')
@login_required
def dashboard():
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).all()
    
    alunos_pendentes = [a for a in todos_alunos if a.status != 'Pago']
    alunos_pagos = [a for a in todos_alunos if a.status == 'Pago']
    
    a_receber = sum((a.valor or 70.0) for a in alunos_pendentes)
    receita_realizada = sum((a.valor or 70.0) for a in alunos_pagos)
    total_trabalhos = len(todos_alunos)
    
    # Matemática dos Gráficos (Chart.js)
    hoje = datetime.utcnow()
    meses_nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
    labels_meses = []
    
    for i in range(5, -1, -1):
        m = hoje.month - i
        y = hoje.year
        if m <= 0: 
            m += 12
            y -= 1
        labels_meses.append((y, m))
        
    faturamento_dict = {(y, m): 0.0 for y, m in labels_meses}
    
    dias_nomes = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    pedidos_dias = [0] * 7
    
    for a in todos_alunos:
        if a.data_cadastro:
            pedidos_dias[a.data_cadastro.weekday()] += 1
            if a.status == 'Pago':
                chave = (a.data_cadastro.year, a.data_cadastro.month)
                if chave in faturamento_dict:
                    faturamento_dict[chave] += (a.valor or 70.0)
                    
    grafico_meses_labels = [f"{meses_nomes[m-1]}/{str(y)[2:]}" for y, m in labels_meses]
    grafico_meses_valores = [faturamento_dict[k] for k in labels_meses]

    uso_modelos = db.session.query(RegistroUso.modelo_usado, db.func.count(RegistroUso.id)).group_by(RegistroUso.modelo_usado).order_by(db.func.count(RegistroUso.id).desc()).all()
    
    return render_template(
        'dashboard.html', 
        a_receber=a_receber, 
        receita_realizada=receita_realizada, 
        total_trabalhos=total_trabalhos, 
        uso_modelos=uso_modelos,
        graf_meses_lbl=grafico_meses_labels, 
        graf_meses_val=grafico_meses_valores,
        graf_dias_lbl=dias_nomes, 
        graf_dias_val=pedidos_dias
    )

# =========================================================
# CONFIGURAÇÕES E PROMPTS
# =========================================================
@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    config = SiteSettings.query.first()
    if request.method == 'POST':
        config.whatsapp_template = request.form.get('whatsapp_template')
        db.session.commit()
        flash('Configurações salvas com sucesso!', 'success')
        return redirect(url_for('configuracoes'))
    return render_template('configuracoes.html', config=config)

@app.route('/prompts', methods=['GET', 'POST'])
@login_required
def gerenciar_prompts():
    if request.method == 'POST':
        novo_prompt = PromptConfig(nome=request.form.get('nome'), texto=request.form.get('texto'))
        db.session.add(novo_prompt)
        db.session.commit()
        flash('Novo Cérebro de IA adicionado!', 'success')
        return redirect(url_for('gerenciar_prompts'))
        
    prompts = PromptConfig.query.all()
    return render_template('prompts.html', prompts=prompts)

@app.route('/prompts/delete/<int:id>')
@login_required
def delete_prompt(id):
    p = PromptConfig.query.get_or_404(id)
    if not p.is_default:
        db.session.delete(p)
        db.session.commit()
    return redirect(url_for('gerenciar_prompts'))

# =========================================================
# CRM, KANBAN E GESTÃO DE CLIENTES
# =========================================================
@app.route('/clientes', methods=['GET', 'POST'])
@login_required
def clientes():
    if request.method == 'POST':
        novo_aluno = Aluno(
            user_id=current_user.id,
            nome=request.form.get('nome'),
            curso=request.form.get('curso'),
            telefone=request.form.get('telefone'),
            valor=float(request.form.get('valor', 70.0)),
            status='Produção'
        )
        db.session.add(novo_aluno)
        db.session.commit()
        flash('Cliente cadastrado na Fila de Produção!', 'success')
        return redirect(url_for('clientes'))
    
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).order_by(Aluno.id.desc()).all()
    config = SiteSettings.query.first()
    
    return render_template('clientes.html', alunos=todos_alunos, config=config)

@app.route('/atualizar_status_kanban/<int:id>', methods=['POST'])
@login_required
def atualizar_status_kanban(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id: 
        return jsonify({'sucesso': False}), 403
        
    aluno.status = request.json.get('status')
    db.session.commit()
    return jsonify({'sucesso': True})

@app.route('/editar_valor/<int:id>', methods=['POST'])
@login_required
def editar_valor(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': abort(403)
    try:
        novo_valor = request.form.get('novo_valor').replace(',', '.')
        aluno.valor = float(novo_valor)
        db.session.commit()
        flash(f'Valor atualizado para R$ {aluno.valor:.2f}', 'success')
    except:
        flash('Valor inválido. Use apenas números.', 'error')
    return redirect(url_for('clientes'))

@app.route('/editar_cliente/<int:id>', methods=['POST'])
@login_required
def editar_cliente(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': abort(403)
    
    aluno.nome = request.form.get('nome')
    aluno.curso = request.form.get('curso')
    aluno.telefone = request.form.get('telefone')
    try: 
        aluno.valor = float(request.form.get('valor').replace(',', '.'))
    except: 
        pass
        
    db.session.commit()
    flash(f'Dados de {aluno.nome} atualizados!', 'success')
    return redirect(url_for('clientes'))

@app.route('/deletar_cliente/<int:id>', methods=['GET'])
@login_required
def deletar_cliente(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': abort(403)
    
    db.session.delete(aluno)
    db.session.commit()
    flash(f'Cliente apagado do sistema.', 'success')
    return redirect(url_for('clientes'))

@app.route('/toggle_status/<int:id>', methods=['POST'])
@login_required
def toggle_status(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': abort(403)
    
    aluno.status = 'Pago' if aluno.status != 'Pago' else 'Pendente'
    db.session.commit()
    return redirect(url_for('clientes'))

@app.route('/cliente/<int:id>', methods=['GET'])
@login_required
def cliente_detalhe(id):
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': abort(403)
    return render_template('cliente_detalhe.html', aluno=aluno)

@app.route('/upload_doc/<int:aluno_id>', methods=['POST'])
@login_required
def upload_doc(aluno_id):
    arquivo = request.files['arquivo']
    if arquivo and arquivo.filename.endswith('.docx'):
        novo_doc = Documento(aluno_id=aluno_id, nome_arquivo=arquivo.filename, dados_arquivo=arquivo.read())
        db.session.add(novo_doc)
        db.session.commit()
        flash('Documento anexado!', 'success')
    return redirect(url_for('cliente_detalhe', id=aluno_id))

@app.route('/download_doc/<int:doc_id>')
@login_required
def download_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    return send_file(io.BytesIO(doc.dados_arquivo), download_name=doc.nome_arquivo, as_attachment=True)

@app.route('/rename_doc/<int:doc_id>', methods=['POST'])
@login_required
def rename_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    novo_nome = request.form.get('novo_nome')
    if not novo_nome.endswith('.docx'): 
        novo_nome += '.docx'
    doc.nome_arquivo = novo_nome
    db.session.commit()
    return redirect(url_for('cliente_detalhe', id=doc.aluno_id))

@app.route('/delete_doc/<int:doc_id>')
@login_required
def delete_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    aluno_id = doc.aluno_id
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for('cliente_detalhe', id=aluno_id))

# =========================================================
# REVISÃO AVULSA
# =========================================================
@app.route('/revisao_avulsa')
@login_required
def revisao_avulsa():
    return render_template('revisao_avulsa.html', modelos=MODELOS_DISPONIVEIS)

@app.route('/avaliar_avulso', methods=['POST'])
@login_required
def avaliar_avulso():
    tema = request.form.get('tema')
    modelo = request.form.get('modelo')
    arquivo = request.files.get('arquivo_trabalho')
    
    if not arquivo or not arquivo.filename.endswith('.docx'):
        return jsonify({"erro": "Envie um arquivo .docx válido."}), 400

    texto_trabalho = extrair_texto_docx(arquivo)
    
    prompt = f"""Você é um professor avaliador extremamente rigoroso. 
Analise o TEMA: {tema} \nE O TRABALHO DO ALUNO: {texto_trabalho}
Faça uma crítica de 3 linhas apontando o que falta para tirar nota máxima. 
REGRA: Seja direto, NUNCA use formatações como negrito (**), itálico, bullet points ou títulos. Responda apenas com texto limpo."""
    try:
        texto_resposta = chamar_ia(prompt, modelo)
        critica_limpa = texto_resposta.replace('*', '').replace('#', '').strip()
        return jsonify({"critica": critica_limpa, "texto_extraido": texto_trabalho})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

@app.route('/corrigir_avulso', methods=['POST'])
@login_required
def corrigir_avulso():
    tema = request.form.get('tema')
    texto_trabalho = request.form.get('texto_extraido')
    critica = request.form.get('critica')
    modelo = request.form.get('modelo')
    
    try:
        respostas = gerar_correcao_ia_tags(tema, texto_trabalho, critica, modelo)
        caminho_padrao = os.path.join(app.root_path, 'TEMPLATE_COM_TAGS.docx')
        with open(caminho_padrao, 'rb') as f: arquivo_memoria = io.BytesIO(f.read())
            
        documento_pronto = preencher_template_com_tags(arquivo_memoria, respostas)
        arquivo_bytes = documento_pronto.read()
        arquivo_base64 = base64.b64encode(arquivo_bytes).decode('utf-8')
        
        return jsonify({"arquivo_base64": arquivo_base64, "nome_arquivo": "Trabalho_Revisado_IA.docx"})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# =========================================================
# LOGIN E ADMINISTRAÇÃO
# =========================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: 
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        else: 
            flash('Credenciais incorretas.', 'error')
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/mudar_senha', methods=['GET', 'POST'])
@login_required
def mudar_senha():
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual')
        nova_senha = request.form.get('nova_senha')
        if check_password_hash(current_user.password, senha_atual):
            current_user.password = generate_password_hash(nova_senha)
            db.session.commit()
            flash('Sua senha foi atualizada!', 'success')
            return redirect(url_for('index'))
    return render_template('mudar_senha.html')

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
    users = User.query.all()
    return render_template('admin.html', users=users, hoje=date.today())

@app.route('/edit_user/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_user(id):
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
    user = User.query.get_or_404(id)
    return render_template('edit_user.html', user=user)

@app.route('/delete_user/<int:id>')
@login_required
def delete_user(id):
    if current_user.role not in ['admin', 'sub-admin']: 
        abort(403)
    user = User.query.get_or_404(id)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True)
