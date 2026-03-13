import os
import io
import re
import base64
import json
import requests
import threading
import logging
import traceback
import hashlib  # <-- BIBLIOTECA RESTAURADA PARA O BANCO DE TEMAS!
import csv
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, abort, send_file, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

# IMPORTAÇÃO DOS NOSSOS NOVOS MÓDULOS
import documentos
import ia_core

app = Flask(__name__)

# =========================================================
# SISTEMA DE LOGS SILENCIOSO & TRATAMENTO DE ERROS GLOBAL
# =========================================================
logging.basicConfig(filename='sistema_erros.log', level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

@app.errorhandler(Exception)
def handle_exception(e):
    logging.error("Falha Interna Detectada:", exc_info=True)
    try: 
        db.session.rollback()
    except Exception: 
        pass
    
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException): 
        return e
        
    return f"""
    <div style="font-family: sans-serif; padding: 20px; background: #262730; color: #E1E4E8; height: 100vh;">
        <h1 style="color: #FF4B4B;">🚨 Ops! Houve um problema.</h1>
        <p>A nossa equipa técnica já foi notificada silenciosamente através do sistema de logs.</p>
        <div style="background: #1A1C23; padding: 15px; border-radius: 5px; color: #FFC107; font-family: monospace; overflow-x: auto;">
            <b>{type(e).__name__}</b>: {str(e)}
        </div>
        <p style="margin-top: 20px;">
            <a href="/" style="color: #2196F3; text-decoration: none;">⬅️ Tentar Voltar à Página Inicial</a>
        </p>
    </div>
    """, 500

# =========================================================
# CONFIGURAÇÕES DO BANCO DE DADOS E SEGURANÇA
# =========================================================
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chave-super-secreta-mude-depois')

db_url = os.environ.get('DATABASE_URL', 'sqlite:///clientes.db')
if db_url.startswith("postgres://"): 
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

if db_url.startswith("postgresql"):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "pool_pre_ping": True, 
        "pool_recycle": 300, 
        "pool_timeout": 30,
        "pool_size": 20,
        "max_overflow": 30
    }
else:
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
    creditos = db.Column(db.Integer, default=0)
    alunos = db.relationship('Aluno', backref='responsavel', lazy=True)
    gabaritos = db.relationship('GabaritoSalvo', backref='dono', lazy=True)

class GabaritoSalvo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    prova_texto = db.Column(db.Text, nullable=False)
    resultado_json = db.Column(db.Text, nullable=False)
    data_geracao = db.Column(db.DateTime, default=datetime.utcnow)

class Aluno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    curso = db.Column(db.String(100))
    telefone = db.Column(db.String(20))
    ava_login = db.Column(db.String(255), nullable=True) 
    ava_senha = db.Column(db.String(255), nullable=True) 
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)
    data_pagamento = db.Column(db.DateTime, nullable=True) 
    status = db.Column(db.String(20), default='Produção') 
    valor = db.Column(db.Float, default=70.0) 
    
    documentos = db.relationship('Documento', backref='aluno', lazy=True, cascade="all, delete-orphan")
    temas = db.relationship('TemaTrabalho', backref='aluno', lazy=True, cascade="all, delete-orphan")

class Documento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    dados_arquivo = db.Column(db.LargeBinary, nullable=False) 
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)

class TemaTrabalho(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    titulo = db.Column(db.String(255), nullable=True) 
    texto = db.Column(db.Text, nullable=False)
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

class PromptConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    texto = db.Column(db.Text, nullable=False)
    is_default = db.Column(db.Boolean, default=False)

class RegistroUso(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime, default=datetime.utcnow)
    modelo_usado = db.Column(db.String(100))
    custo = db.Column(db.Float, default=0.0)

class SiteSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    whatsapp_template = db.Column(
        db.Text, 
        default="Olá {nome}, seu trabalho de {curso} ficou pronto com excelência! 🎉\nO valor acordado foi R$ {valor}.\n\nSegue a minha chave PIX para liberação do arquivo: [SUA CHAVE AQUI]"
    )
    prompt_password = db.Column(db.String(255), nullable=True)
    convert_api_key = db.Column(db.String(255), nullable=True)
    modelos_ativos = db.Column(db.Text, nullable=True)

class GeracaoTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='Pendente') 
    resultado = db.Column(db.Text, nullable=True) 
    modelo_utilizado = db.Column(db.String(100), nullable=True)
    erro = db.Column(db.Text, nullable=True)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

PROMPT_REGRAS_BASE = """VOCÊ AGORA ASSUME A PERSONA DE UM PROFESSOR UNIVERSITÁRIO AVALIADOR EXTREMAMENTE RIGOROSO E DE ALTA EXCELÊNCIA ACADÊMICA.

REGRAS DE VOCABULÁRIO (ESTRITAMENTE PROIBIDO):
É proibido usar as seguintes palavras ou expressões: momentum, locus, outrossim, dessarte, destarte, mergulho profundo, testamento, tapeçaria, farol, crucial, vital, paisagem, adentrar, notável.
Não use formatação em itálico (asterisco simples) para termos em inglês como soft skills ou hard skills."""

# =========================================================
# INICIALIZAÇÃO BLINDADA E MIGRAÇÕES
# =========================================================
with app.app_context():
    db.create_all()
    # CORREÇÃO AQUI: "user" entre aspas duplas para evitar conflito no PostgreSQL
    try: db.session.execute(db.text('ALTER TABLE "user" ADD COLUMN creditos INTEGER DEFAULT 0')); db.session.commit()
    except Exception: db.session.rollback()
    
    try: db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN status VARCHAR(20) DEFAULT 'Produção'")); db.session.commit()
    except Exception: db.session.rollback()
    try: db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN valor FLOAT DEFAULT 70.0")); db.session.commit()
    except Exception: db.session.rollback()
    try: db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN ava_login VARCHAR(255)")); db.session.commit()
    except Exception: db.session.rollback()
    try: db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN ava_senha VARCHAR(255)")); db.session.commit()
    except Exception: db.session.rollback()
    try: db.session.execute(db.text("ALTER TABLE site_settings ADD COLUMN prompt_password VARCHAR(255)")); db.session.commit()
    except Exception: db.session.rollback()
    try: db.session.execute(db.text("ALTER TABLE site_settings ADD COLUMN convert_api_key VARCHAR(255)")); db.session.commit()
    except Exception: db.session.rollback()
    try: db.session.execute(db.text("ALTER TABLE registro_uso ADD COLUMN custo FLOAT DEFAULT 0.0")); db.session.commit()
    except Exception: db.session.rollback()
    try: db.session.execute(db.text("ALTER TABLE aluno ADD COLUMN data_pagamento TIMESTAMP")); db.session.commit()
    except Exception: db.session.rollback()
    try: db.session.execute(db.text("ALTER TABLE site_settings ADD COLUMN modelos_ativos TEXT")); db.session.commit()
    except Exception: db.session.rollback()

    try:
        alunos_pagos = Aluno.query.filter_by(status='Pago').all()
        for al in alunos_pagos:
            if not al.data_pagamento:
                al.data_pagamento = al.data_cadastro
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        if not User.query.filter_by(username='admin').first():
            senha_hash = generate_password_hash('admin123')
            admin_user = User(username='admin', password=senha_hash, role='admin', creditos=0)
            db.session.add(admin_user)
            db.session.commit()
        if not PromptConfig.query.filter_by(is_default=True).first():
            novo_prompt = PromptConfig(nome="Padrão Oficial (Desafio UNIASSELVI)", texto=PROMPT_REGRAS_BASE, is_default=True)
            db.session.add(novo_prompt)
            db.session.commit()
        if not SiteSettings.query.first():
            db.session.add(SiteSettings())
            db.session.commit()
    except Exception: 
        db.session.rollback()

# =========================================================
# GESTÃO DINÂMICA DE MODELOS DE IA
# =========================================================
TODOS_MODELOS_CONHECIDOS = [
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-opus",
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "google/gemini-2.5-pro",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "qwen/qwen-2.5-72b-instruct",
    "mistralai/mistral-large",
    "x-ai/grok-2-vision"
]

def get_modelos_ativos():
    config = SiteSettings.query.first()
    if config and config.modelos_ativos:
        return [m.strip() for m in config.modelos_ativos.split(',') if m.strip()]
    return ["anthropic/claude-3.5-sonnet", "google/gemini-2.5-pro", "google/gemini-2.5-flash", "meta-llama/llama-3.3-70b-instruct", "qwen/qwen-2.5-72b-instruct"]

def executar_geracao_bg(task_id, prompt_completo, fila_modelos):
    with app.app_context():
        ultimo_erro = ""
        modelos_para_tentar = fila_modelos[:2]
        
        for modelo in modelos_para_tentar:
            try:
                texto_resposta, custo_estimado = ia_core.chamar_ia(prompt_completo, modelo, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
                dicionario = ia_core.extrair_dicionario(texto_resposta)
                
                tags_preenchidas = sum(1 for v in dicionario.values() if v.strip())
                
                if tags_preenchidas < 3: 
                    raise Exception(f"A IA {modelo} falhou severamente ao preencher as tags.")
                
                task_verificar = GeracaoTask.query.get(task_id)
                if task_verificar and task_verificar.status == 'Cancelado':
                    db.session.rollback()
                    return 
                    
                novo_registro = RegistroUso(modelo_usado=modelo, custo=custo_estimado)
                db.session.add(novo_registro)
                
                task_verificar.status = 'Concluido'
                task_verificar.resultado = json.dumps(dicionario)
                task_verificar.modelo_utilizado = modelo
                db.session.commit()
                return
                
            except Exception as e:
                ultimo_erro = str(e)
                continue
                
        task_erro = GeracaoTask.query.get(task_id)
        if task_erro and task_erro.status != 'Cancelado':
            task_erro.status = 'Erro'
            task_erro.erro = f"Falha ao processar as IAs. Último erro: {ultimo_erro}"
            db.session.commit()

# =========================================================
# ROTAS PÚBLICAS E AUTENTICAÇÃO
# =========================================================
@app.route('/')
@login_required
def index():
    if current_user.role == 'aluno':
        return redirect(url_for('area_aluno'))
        
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).order_by(Aluno.id.desc()).all()
    alunos_ativos = [a for a in todos_alunos if a.status == 'Produção' or not a.status]
    prompts = PromptConfig.query.all()
    return render_template('index.html', modelos=get_modelos_ativos(), alunos=alunos_ativos, prompts=prompts)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: 
        return redirect(url_for('index'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        flash('Credenciais incorretas.', 'error')
    return render_template('login.html')

@app.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    if current_user.is_authenticated: 
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if User.query.filter_by(username=username).first():
            flash('Este usuário já existe. Tente fazer login.', 'error')
            return redirect(url_for('cadastro'))
            
        novo_aluno = User(username=username, password=generate_password_hash(password), role='aluno', creditos=0)
        db.session.add(novo_aluno)
        db.session.commit()
        
        login_user(novo_aluno)
        return redirect(url_for('area_aluno'))
    return render_template('cadastro.html')

@app.route('/logout')
@login_required
def logout(): 
    logout_user()
    return redirect(url_for('login'))

@app.route('/mudar_senha', methods=['GET', 'POST'])
@login_required
def mudar_senha():
    if request.method == 'POST':
        if check_password_hash(current_user.password, request.form.get('senha_atual')):
            current_user.password = generate_password_hash(request.form.get('nova_senha')); db.session.commit()
            flash('Sua senha foi atualizada!', 'success'); return redirect(url_for('index'))
    return render_template('mudar_senha.html')

# =========================================================
# NOVA FERRAMENTA: PROJETOS DE EXTENSÃO
# =========================================================
@app.route('/projetos_extensao')
@login_required
def projetos_extensao():
    if current_user.role == 'aluno': abort(403)
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).order_by(Aluno.nome).all()
    return render_template('projetos_extensao.html', alunos=todos_alunos)

@app.route('/gerar_extensao', methods=['POST'])
@login_required
def gerar_extensao():
    if current_user.role == 'aluno': abort(403)
    
    aluno_id = request.form.get('aluno_id')
    matricula = request.form.get('matricula', 'Não informada')
    nome_avulso = request.form.get('nome_avulso', '')
    curso_avulso = request.form.get('curso_avulso', '')
    data_inicial_str = request.form.get('data_inicial', '')
    
    arquivo_template = request.files.get('template_doc')
    if not arquivo_template or not arquivo_template.filename.endswith('.docx'):
        flash('Envie um arquivo de Template .docx válido (Ex: Template Oficial Paraná).', 'error')
        return redirect(url_for('projetos_extensao'))

    aluno = Aluno.query.get(aluno_id) if aluno_id else None
    nome_aluno = aluno.nome if aluno else nome_avulso
    curso_aluno = aluno.curso if aluno else curso_avulso

    # Dicionário base de substituição para as tags no Word
    dicionario = {
        "{{NOME_ALUNO}}": nome_aluno,
        "{{MATRICULA}}": matricula,
        "{{CURSO}}": curso_aluno
    }

    # Se forneceu uma data inicial, gera até 30 datas consecutivas pulando os Domingos
    if data_inicial_str:
        try:
            data_atual = datetime.strptime(data_inicial_str, '%Y-%m-%d')
            for i in range(1, 31):
                dicionario[f"{{{{DATA_{i}}}}}"] = data_atual.strftime('%d/%m/%Y')
                data_atual += timedelta(days=1)
                if data_atual.weekday() == 6: # Se for 6 (Domingo), pula +1 dia (para Segunda)
                    data_atual += timedelta(days=1)
        except Exception:
            pass

    try:
        arquivo_memoria = io.BytesIO(arquivo_template.read())
        # Chama a nova função do documentos.py que preserva a formatação!
        doc_pronto = documentos.preencher_template_extensao(arquivo_memoria, dicionario)
        doc_bytes = doc_pronto.read()
        nome_saida = f"Projeto_Extensao_{nome_aluno.replace(' ', '_')}.docx"

        if aluno:
            novo_doc = Documento(aluno_id=aluno.id, nome_arquivo=nome_saida, dados_arquivo=doc_bytes)
            db.session.add(novo_doc)
            db.session.commit()
            flash('Projeto de Extensão gerado e salvo na pasta do aluno com sucesso!', 'success')
            return redirect(url_for('cliente_detalhe', id=aluno.id))
        else:
            return send_file(
                io.BytesIO(doc_bytes),
                as_attachment=True,
                download_name=nome_saida,
                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            )
    except Exception as e:
        flash(f'Erro ao processar o documento: {str(e)}', 'error')
        return redirect(url_for('projetos_extensao'))

# =========================================================
# GABARITO E ÁREA DO ALUNO
# =========================================================
@app.route('/area_aluno')
@login_required
def area_aluno():
    if current_user.role != 'aluno':
        return redirect(url_for('index'))
    historico = GabaritoSalvo.query.filter_by(user_id=current_user.id).order_by(GabaritoSalvo.id.desc()).all()
    return render_template('area_aluno.html', historico=historico)

@app.route('/gabarito_inteligente')
@login_required
def gabarito_inteligente():
    if current_user.role == 'aluno': abort(403)
    return render_template('gabarito_inteligente.html')

@app.route('/api/gerar_gabarito', methods=['POST'])
@login_required
def api_gerar_gabarito():
    if current_user.role == 'aluno': abort(403)
    texto_prova = request.form.get('prova', '')
    if not texto_prova: return jsonify({"sucesso": False, "erro": "O texto da prova está vazio."})

    modelo_elite = "anthropic/claude-3.5-sonnet"
    prompt = f"Resolva a prova abaixo. Retorne EXATAMENTE um Array JSON puro.\nEstrutura: [{{\"questao\": 1, \"resposta\": \"A\", \"justificativa\": \"Motivo.\"}}]\nPROVA:\n{texto_prova}"
    
    try:
        resposta_ia, custo = ia_core.chamar_ia(prompt, modelo_elite, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
        resultado_ia = ia_core.extrair_json_seguro(resposta_ia)
    except Exception as e:
        return jsonify({"sucesso": False, "erro": f"Falha na IA: {e}"})

    if not resultado_ia: return jsonify({"sucesso": False, "erro": "A IA falhou ao processar o gabarito."})

    db.session.add(RegistroUso(modelo_usado=modelo_elite, custo=custo))
    db.session.commit()

    gabarito_final = []
    for idx, q in enumerate(resultado_ia):
        letra_crua = str(q.get('resposta', '')).upper().strip()
        match_letra = re.search(r'[A-E]', letra_crua)
        gabarito_final.append({
            "questao": q.get('questao', idx + 1),
            "resposta": match_letra.group(0) if match_letra else "?",
            "justificativa": q.get('justificativa', 'Sem justificativa.')
        })

    return jsonify({"sucesso": True, "gabarito": gabarito_final, "modelo_utilizado": modelo_elite})

# =========================================================
# ROTAS DE TRABALHOS (GERAÇÃO/LEITURA)
# =========================================================
@app.route('/api/temas/<int:aluno_id>')
@login_required
def get_temas_aluno(aluno_id):
    if current_user.role == 'aluno': abort(403)
    temas = TemaTrabalho.query.filter_by(aluno_id=aluno_id).all()
    lista_temas = [{"id": t.id, "titulo": t.titulo, "texto": t.texto} for t in temas]
    return jsonify(lista_temas)

@app.route('/api/extrair_memorial/<int:doc_id>', methods=['GET'])
@login_required
def api_extrair_memorial(doc_id):
    if current_user.role == 'aluno': abort(403)
    doc = Documento.query.get_or_404(doc_id)
    if doc.aluno.user_id != current_user.id and current_user.role != 'admin':
        return jsonify({"sucesso": False, "erro": "Acesso negado."}), 403
    try:
        sucesso, texto_memorial = documentos.extrair_etapa_5(doc.dados_arquivo)
        return jsonify({"sucesso": sucesso, "texto": texto_memorial if sucesso else "", "erro": texto_memorial if not sucesso else ""})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": f"Erro interno ao ler o documento: {str(e)}"})

@app.route('/gerar_rascunho', methods=['POST'])
@login_required
def gerar_rascunho():
    if current_user.role == 'aluno': abort(403)
    tema = request.form.get('tema')
    modelo_selecionado = request.form.get('modelo')
    prompt_id = request.form.get('prompt_id')
    
    if prompt_id and str(prompt_id).isdigit():
        config = PromptConfig.query.get(int(prompt_id))
    else:
        config = PromptConfig.query.filter_by(is_default=True).first()
        
    texto_prompt = config.texto if config else PROMPT_REGRAS_BASE
    prompt_completo = f"TEMA:\n{tema}\n\n{texto_prompt}\n\nMUITO IMPORTANTE: Use as marcações exatas [START_NOME_DA_TAG] e [END_NOME_DA_TAG] para cada sessão do trabalho."
    
    fila_modelos = [modelo_selecionado] + [m for m in get_modelos_ativos() if m != modelo_selecionado]
    nova_task = GeracaoTask(user_id=current_user.id, status='Pendente')
    db.session.add(nova_task)
    db.session.commit()
    
    threading.Thread(target=executar_geracao_bg, args=(nova_task.id, prompt_completo, fila_modelos)).start()
    return jsonify({"sucesso": True, "task_id": nova_task.id})

@app.route('/humanizar_trabalho', methods=['POST'])
@login_required
def humanizar_trabalho():
    if current_user.role == 'aluno': abort(403)
    try:
        dados = request.json
        tema = dados.get('tema', '')
        modelo_selecionado = dados.get('modelo', get_modelos_ativos()[0])
        prompt_id = dados.get('prompt_id')
        contexto_atual = dados.get('dicionario', {}) 

        if prompt_id and str(prompt_id).isdigit():
            config = PromptConfig.query.get(int(prompt_id))
        else:
            config = PromptConfig.query.filter_by(is_default=True).first()
            
        texto_prompt = config.texto if config else PROMPT_REGRAS_BASE
        texto_contexto = ""
        for k, v in contexto_atual.items():
            if v and str(v).strip():
                tag_limpa = k.replace("{{", "").replace("}}", "")
                texto_contexto += f"[START_{tag_limpa}]\n{v}\n[END_{tag_limpa}]\n\n"

        prompt_humanizador = f"""ATENÇÃO: O SEU ÚNICO PAPEL AGORA É SER UM REVISOR DE ESTILO E PARAFRASEADOR. 
É ESTRITAMENTE PROIBIDO INVENTAR ASSUNTOS, PERSONAGENS OU CONCEITOS NOVOS.

O texto abaixo já está pronto e correto. O seu trabalho é APENAS reescrever AS MESMAS INFORMAÇÕES, OS MESMOS FATOS, E OS MESMOS PERSONAGENS, mas com um estilo de escrita diferente para não ser detectado por softwares de plágio de IA (Turnitin/GPTZero).

REGRAS DE BLINDAGEM (ESTILO DE ESCRITA):
1. Explosividade (Burstiness): Alterne entre frases longas e explicativas e frases extremamente curtas e diretas.
2. Perplexidade: Troque termos robóticos por sinônimos menos óbvios.

TEXTO ORIGINAL:
{texto_contexto}

IMPORTANTE: Retorne o resultado usando EXATAMENTE as mesmas tags [START_nome_da_tag] e [END_nome_da_tag]."""
        
        fila_modelos = [modelo_selecionado] + [m for m in get_modelos_ativos() if m != modelo_selecionado]
        nova_task = GeracaoTask(user_id=current_user.id, status='Pendente')
        db.session.add(nova_task)
        db.session.commit()
        
        threading.Thread(target=executar_geracao_bg, args=(nova_task.id, prompt_humanizador, fila_modelos)).start()
        
        return jsonify({"sucesso": True, "task_id": nova_task.id})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/cancelar_geracao/<int:task_id>', methods=['POST'])
@login_required
def cancelar_geracao(task_id):
    if current_user.role == 'aluno': abort(403)
    task = GeracaoTask.query.get(task_id)
    if task and task.user_id == current_user.id and task.status == 'Pendente':
        task.status = 'Cancelado'
        db.session.commit()
        return jsonify({"sucesso": True})
    return jsonify({"sucesso": False, "erro": "Tarefa não encontrada ou já concluída."})

@app.route('/status_geracao/<int:task_id>', methods=['GET'])
@login_required
def status_geracao(task_id):
    if current_user.role == 'aluno': abort(403)
    task = GeracaoTask.query.get(task_id)
    if not task or task.user_id != current_user.id: return jsonify({"sucesso": False, "erro": "Tarefa não encontrada."})
    if task.status == 'Pendente': return jsonify({"status": "Pendente"})
    if task.status == 'Erro': return jsonify({"status": "Erro", "erro": task.erro})
    if task.status == 'Cancelado': return jsonify({"status": "Cancelado"})
    return jsonify({"status": "Concluido", "dicionario": json.loads(task.resultado), "modelo_utilizado": task.modelo_utilizado})

@app.route('/analisar_ia_trecho', methods=['POST'])
@login_required
def analisar_ia_trecho():
    if current_user.role == 'aluno': abort(403)
    try:
        dados = request.json or {}
        trecho = str(dados.get('trecho', '')).strip()
        
        if not trecho or len(trecho) < 25:
            return jsonify({"sucesso": True, "porcentagem": 0})
            
        modelo_rapido = "google/gemini-2.5-flash"
        prompt = f"""Você é um analista de textos acadêmicos. Avalie de 0 a 100 qual a probabilidade do texto abaixo ter sido gerado por uma Inteligência Artificial.
ATENÇÃO: Textos universitários usam naturalmente linguagem culta. NÃO confunda texto bem escrito e formal com Inteligência Artificial!

DÊ UMA NOTA ALTA (70-100%) APENAS SE: 
- Houver excesso de palavras-clichê robóticas (ex: 'crucial', 'mergulho profundo', 'tapeçaria', 'vital', 'locus', 'notável', 'farol', 'em suma').
- O texto não tiver variação de tamanho de frases (texto "quadrado" e monótono).

DÊ UMA NOTA BAIXA (0-30%) SE: 
- For direto, objetivo, e as frases tiverem tamanhos desiguais.

TEXTO:
{trecho}

Responda ÚNICA E EXCLUSIVAMENTE com o número da porcentagem (ex: 15). Nenhuma palavra extra."""

        resposta, custo = ia_core.chamar_ia(prompt, modelo_rapido, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
        db.session.add(RegistroUso(modelo_usado=modelo_rapido, custo=custo))
        db.session.commit()
        numeros = re.findall(r'\d+', resposta)
        porcentagem = int(numeros[0]) if numeros else 15
        if porcentagem > 100: porcentagem = 100
        return jsonify({"sucesso": True, "porcentagem": porcentagem})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/humanizar_trecho_avulso', methods=['POST'])
@login_required
def humanizar_trecho_avulso():
    if current_user.role == 'aluno': abort(403)
    try:
        dados = request.json or {}
        trecho = str(dados.get('trecho', ''))
        modelo = dados.get('modelo', get_modelos_ativos()[0])
        prompt = f"""Sua missão é reescrever o texto abaixo como se fosse um estudante universitário real, visando 0% de detecção por softwares Anti-IA (GPTZero).

TÁTICAS OBRIGATÓRIAS:
1. Exploda o padrão de frases: Escreva uma frase curta e cortante. Depois, escreva uma mais explicativa e longa.
2. Troque o vocabulário "chique" por palavras comuns de faculdade. 
3. É ESTRITAMENTE PROIBIDO usar palavras como: crucial, notável, vital, mergulhar, adentrar, em resumo, outrossim, farol, tapeçaria, locus.
4. Não adicione saudações, apenas o conteúdo limpo reescrito.

TEXTO ORIGINAL:
{trecho}

Retorne APENAS o novo texto."""
        
        novo_texto, custo = ia_core.chamar_ia(prompt, modelo, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
        db.session.add(RegistroUso(modelo_usado=modelo, custo=custo))
        db.session.commit()
        while novo_texto.startswith('**') and novo_texto.endswith('**') and len(novo_texto) > 4: 
            novo_texto = novo_texto[2:-2].strip()
        return jsonify({"sucesso": True, "novo_texto": novo_texto})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/assistente_pontual', methods=['POST'])
@login_required
def assistente_pontual():
    if current_user.role == 'aluno': abort(403)
    try:
        dados = request.json or {}
        trecho = str(dados.get('trecho', ''))
        comando = str(dados.get('comando', ''))
        modelo = dados.get('modelo', get_modelos_ativos()[0])
        prompt = f"Você é um assistente de edição académica de elite.\nTEXTO ORIGINAL:\n{trecho}\n\nPEDIDO DO USUÁRIO:\n{comando}\n\nReescreva o texto original aplicando EXATAMENTE o que foi pedido. Responda APENAS com o novo texto limpo, sem marcações markdown (**)."
        novo_texto, custo = ia_core.chamar_ia(prompt, modelo, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
        db.session.add(RegistroUso(modelo_usado=modelo, custo=custo))
        db.session.commit()
        return jsonify({"sucesso": True, "novo_texto": novo_texto})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/exterminar_cliches', methods=['POST'])
@login_required
def exterminar_cliches():
    if current_user.role == 'aluno': abort(403)
    try:
        dados = request.json or {}
        trecho = str(dados.get('trecho', ''))
        modelo_rapido = "google/gemini-2.5-flash"
        prompt = f"""Você é um editor humano de textos acadêmicos implacável. Sua missão é limpar este parágrafo.
TEXTO ORIGINAL:
{trecho}
REGRAS: 
1. Remova palavras clichês de IA (ex: locus, momentum, tapeçaria, mergulho profundo, crucial, outrossim, dessarte, adentrar, vital, testamento, notável, farol).
2. Substitua por português moderno.
3. Mantenha 100% do tamanho e do sentido.
Retorne APENAS o texto limpo."""
        novo_texto, custo = ia_core.chamar_ia(prompt, modelo_rapido, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
        db.session.add(RegistroUso(modelo_usado=modelo_rapido, custo=custo))
        db.session.commit()
        return jsonify({"sucesso": True, "novo_texto": novo_texto})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/regerar_trecho', methods=['POST'])
@login_required
def regerar_trecho():
    if current_user.role == 'aluno': abort(403)
    try:
        dados = request.json or {}
        tema = str(dados.get('tema', ''))
        tag = str(dados.get('tag', ''))
        modelo_selecionado = dados.get('modelo', get_modelos_ativos()[0])
        prompt_id = dados.get('prompt_id')
        contexto_atual = dados.get('dicionario', {}) 

        if prompt_id and str(prompt_id).isdigit():
            config = PromptConfig.query.get(int(prompt_id))
        else:
            config = PromptConfig.query.filter_by(is_default=True).first()
            
        texto_prompt = config.texto if config else PROMPT_REGRAS_BASE
        texto_contexto = "".join([f"{k}:\n{v}\n\n" for k, v in contexto_atual.items() if v and str(v).strip()])

        prompt_regeracao = f"""Você é um professor avaliador rigoroso.
TEMA/CASO DO DESAFIO:\n{tema}
CONTEXTO ATUAL DO TRABALHO:\n{texto_contexto}
REGRAS GERAIS:\n{texto_prompt}
TAREFA: Reescreva APENAS o trecho da tag {tag}. É OBRIGATÓRIO fazer sentido com o contexto. NÃO inclua marcações [START]. Retorne APENAS o texto limpo."""
        
        fila_modelos = [modelo_selecionado] + [m for m in get_modelos_ativos() if m != modelo_selecionado]
        ultimo_erro = ""

        for modelo in fila_modelos[:2]: 
            try:
                novo_texto, custo = ia_core.chamar_ia(prompt_regeracao, modelo, CHAVE_API_GOOGLE, CHAVE_OPENROUTER)
                novo_texto = re.sub(rf"\[/?START_{tag}\]", "", novo_texto, flags=re.IGNORECASE)
                novo_texto = re.sub(rf"\[/?END_{tag}\]", "", novo_texto, flags=re.IGNORECASE).strip()
                while novo_texto.startswith('**') and novo_texto.endswith('**') and len(novo_texto) > 4: 
                    novo_texto = novo_texto[2:-2].strip()
                    
                db.session.add(RegistroUso(modelo_usado=modelo, custo=custo))
                db.session.commit()
                return jsonify({"sucesso": True, "novo_texto": novo_texto, "modelo_utilizado": modelo})
            except Exception as e:
                ultimo_erro = str(e)
                continue
                
        return jsonify({"sucesso": False, "erro": f"Falha ao regerar o trecho. Erro: {ultimo_erro}"})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/gerar_docx_final', methods=['POST'])
@login_required
def gerar_docx_final():
    if current_user.role == 'aluno': abort(403)
    try:
        dados = request.json or {}
        aluno_id = dados.get('aluno_id')
        dicionario_editado = dados.get('dicionario', {})
        nome_arquivo = str(dados.get('nome_arquivo', '')).strip()
        
        caminho_padrao = os.path.join(app.root_path, 'TEMPLATE_COM_TAGS.docx')
        with open(caminho_padrao, 'rb') as f: 
            arquivo_memoria = io.BytesIO(f.read())
            
        documento_pronto = documentos.preencher_template_com_tags(arquivo_memoria, dicionario_editado)
        arquivo_bytes = documento_pronto.read()
        
        if nome_arquivo:
            if not nome_arquivo.lower().endswith('.docx'): nome_arquivo += '.docx'
        else:
            nome_arquivo = f"Trabalho_{datetime.now().strftime('%d%m%Y')}.docx"
            
        if aluno_id:
            novo_doc = Documento(aluno_id=aluno_id, nome_arquivo=nome_arquivo, dados_arquivo=arquivo_bytes)
            db.session.add(novo_doc)
            aluno = Aluno.query.get(aluno_id)
            if aluno and (aluno.status == 'Produção' or aluno.status is None): 
                aluno.status = 'Pendente'
            db.session.commit()
            
        return jsonify({"sucesso": True, "nome_arquivo": nome_arquivo, "arquivo_base64": base64.b64encode(arquivo_bytes).decode('utf-8')})
    except Exception as e: 
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/converter_pdf/<int:doc_id>', methods=['POST'])
@login_required
def converter_pdf(doc_id):
    if current_user.role == 'aluno': abort(403)
    try:
        doc = Documento.query.get_or_404(doc_id)
        if doc.aluno.user_id != current_user.id and current_user.role != 'admin': return jsonify({"sucesso": False, "erro": "Acesso negado."}), 403
        if not doc.nome_arquivo.lower().endswith('.docx'): return jsonify({"sucesso": False, "erro": "Apenas arquivos .docx podem ser convertidos."})

        config = SiteSettings.query.first()
        convert_api_key = config.convert_api_key if config else None
        if not convert_api_key: return jsonify({"sucesso": False, "erro": "Cadastre a sua Secret Key da ConvertAPI nas Configurações."})

        response = requests.post(f'https://v2.convertapi.com/convert/docx/to/pdf?Secret={convert_api_key}', files={'File': (doc.nome_arquivo, doc.dados_arquivo)})
        dados_resposta = response.json()
        
        if response.status_code == 200:
            pdf_bytes = base64.b64decode(dados_resposta['Files'][0]['FileData'])
            novo_nome = doc.nome_arquivo.replace('.docx', '.pdf').replace('.DOCX', '.pdf')
            db.session.add(Documento(aluno_id=doc.aluno_id, nome_arquivo=novo_nome, dados_arquivo=pdf_bytes))
            db.session.commit()
            return jsonify({"sucesso": True})
        else:
            return jsonify({"sucesso": False, "erro": f"A API recusou: {dados_resposta.get('Message', 'Falha desconhecida.')}"})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/banco_temas')
@login_required
def banco_temas():
    if current_user.role == 'aluno': abort(403)
    temas_brutos = db.session.query(TemaTrabalho).join(Aluno).filter(Aluno.user_id == current_user.id).order_by(TemaTrabalho.data_cadastro.desc()).all()
    temas_unicos = []
    hashes_vistos = set()
    for t in temas_brutos:
        texto_limpo = t.texto.strip()
        if not texto_limpo: continue
        texto_puro_para_comparacao = re.sub(r'[\W_]+', '', texto_limpo[:500].lower())
        texto_hash = hashlib.md5(texto_puro_para_comparacao.encode('utf-8')).hexdigest()
        
        if texto_hash not in hashes_vistos:
            hashes_vistos.add(texto_hash)
            linhas = [linha.strip() for linha in texto_limpo.split('\n') if linha.strip()]
            titulo_extraido = ""
            for linha in linhas[:5]:
                if "DESAFIO" in linha.upper():
                    titulo_extraido = linha.upper().replace('*', '').strip()
                    break
            if not titulo_extraido and linhas and len(linhas[0]) < 100:
                titulo_extraido = linhas[0].upper().replace('*', '').strip()
            
            titulo_atual = str(t.titulo).strip().lower() if t.titulo else ""
            if titulo_extraido: t.titulo_exibicao = titulo_extraido
            elif not titulo_atual or titulo_atual.startswith("tema"):
                nome_curso = str(t.aluno.curso).strip().upper() if t.aluno.curso and t.aluno.curso.strip() else "DISCIPLINA NÃO INFORMADA"
                t.titulo_exibicao = f"DESAFIO PROFISSIONAL DE {nome_curso}"
            else: t.titulo_exibicao = str(t.titulo).upper()
            temas_unicos.append(t)
            
    return render_template('banco_temas.html', temas=temas_unicos)

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'aluno': abort(403)
    data_inicio_str = request.args.get('data_inicio')
    data_fim_str = request.args.get('data_fim')
    agora_utc = datetime.utcnow()
    hoje_brasil = agora_utc - timedelta(hours=3)
    data_inicio = None
    data_fim = None
    if data_inicio_str:
        try: data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d').date()
        except: pass
    if data_fim_str:
        try: data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d').date()
        except: pass

    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).all()
    receita_hoje, receita_periodo, a_receber_periodo, trabalhos_periodo = 0.0, 0.0, 0.0, 0

    for a in todos_alunos:
        if a.status == 'Pago':
            d_base = a.data_pagamento if a.data_pagamento else a.data_cadastro
            if d_base and (d_base - timedelta(hours=3)).date() == hoje_brasil.date():
                receita_hoje += (a.valor or 70.0)

        data_ref_pagamento = (a.data_pagamento - timedelta(hours=3)).date() if a.data_pagamento else ((a.data_cadastro - timedelta(hours=3)).date() if a.data_cadastro else hoje_brasil.date())
        data_ref_cadastro = (a.data_cadastro - timedelta(hours=3)).date() if a.data_cadastro else hoje_brasil.date()

        in_period_pagamento = True
        in_period_cadastro = True
        if data_inicio:
            if data_ref_pagamento < data_inicio: in_period_pagamento = False
            if data_ref_cadastro < data_inicio: in_period_cadastro = False
        if data_fim:
            if data_ref_pagamento > data_fim: in_period_pagamento = False
            if data_ref_cadastro > data_fim: in_period_cadastro = False

        if a.status == 'Pago' and in_period_pagamento: receita_periodo += (a.valor or 70.0)
        if a.status != 'Pago' and in_period_cadastro: a_receber_periodo += (a.valor or 70.0)
        if in_period_cadastro: trabalhos_periodo += 1
            
    todos_usos = RegistroUso.query.all()
    custo_periodo = 0.0
    uso_modelos_dict = {}

    for u in todos_usos:
        d_uso_br = (u.data - timedelta(hours=3)).date()
        in_period_uso = True
        if data_inicio and d_uso_br < data_inicio: in_period_uso = False
        if data_fim and d_uso_br > data_fim: in_period_uso = False

        if in_period_uso:
            custo_periodo += (u.custo or 0.0)
            if u.modelo_usado not in uso_modelos_dict:
                uso_modelos_dict[u.modelo_usado] = {'count': 0, 'custo': 0.0}
            uso_modelos_dict[u.modelo_usado]['count'] += 1
            uso_modelos_dict[u.modelo_usado]['custo'] += (u.custo or 0.0)

    uso_modelos_lista = [(k, v['count'], v['custo']) for k, v in uso_modelos_dict.items()]
    
    labels_meses = []
    for i in range(5, -1, -1):
        m = hoje_brasil.month - i
        y = hoje_brasil.year
        if m <= 0: m += 12; y -= 1
        labels_meses.append((y, m))
        
    faturamento_dict = {(y, m): 0.0 for y, m in labels_meses}
    pedidos_dias = [0] * 7
    for a in todos_alunos:
        if a.data_cadastro:
            data_cad_brasil = a.data_cadastro - timedelta(hours=3)
            pedidos_dias[data_cad_brasil.weekday()] += 1
            if a.status == 'Pago':
                data_base = a.data_pagamento if a.data_pagamento else a.data_cadastro
                if data_base:
                    data_base_brasil = data_base - timedelta(hours=3)
                    chave = (data_base_brasil.year, data_base_brasil.month)
                    if chave in faturamento_dict: faturamento_dict[chave] += (a.valor or 70.0)
                    
    meses_nomes = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    grafico_meses_labels = [f"{meses_nomes[m-1]}/{str(y)[2:]}" for y, m in labels_meses]
    grafico_meses_valores = [faturamento_dict[k] for k in labels_meses]
    
    saldo_real_openrouter = ia_core.consultar_saldo_openrouter(CHAVE_OPENROUTER)
    
    return render_template('dashboard.html', 
                           receita_hoje=receita_hoje, 
                           receita_periodo=receita_periodo, 
                           a_receber_periodo=a_receber_periodo, 
                           custo_periodo=custo_periodo, 
                           saldo_real_openrouter=saldo_real_openrouter, 
                           trabalhos_periodo=trabalhos_periodo, 
                           uso_modelos=uso_modelos_lista, 
                           graf_meses_lbl=grafico_meses_labels, 
                           graf_meses_val=grafico_meses_valores, 
                           graf_dias_lbl=['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'], 
                           graf_dias_val=pedidos_dias,
                           data_inicio=data_inicio_str or '',
                           data_fim=data_fim_str or '',
                           filtrado=(bool(data_inicio_str) or bool(data_fim_str)))

@app.route('/clientes', methods=['GET', 'POST'])
@login_required
def clientes():
    if current_user.role == 'aluno': abort(403)
    if request.method == 'POST':
        try: valor_float = float(request.form.get('valor', '70.0').replace(',', '.'))
        except ValueError: valor_float = 70.0
        db.session.add(Aluno(user_id=current_user.id, nome=request.form.get('nome'), curso=request.form.get('curso'), telefone=request.form.get('telefone'), ava_login=request.form.get('ava_login'), ava_senha=request.form.get('ava_senha'), valor=valor_float, status='Produção'))
        db.session.commit(); flash('Cliente cadastrado!', 'success')
        return redirect(url_for('clientes'))
        
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).order_by(Aluno.id.desc()).all()
    alunos_pendentes = [a for a in todos_alunos if a.status != 'Pago']
    alunos_pagos_brutos = [a for a in todos_alunos if a.status == 'Pago']

    agora_utc = datetime.utcnow()
    hoje_brasil = (agora_utc - timedelta(hours=3)).date()
    ontem_brasil = hoje_brasil - timedelta(days=1)

    pagos_agrupados = {}
    for a in alunos_pagos_brutos:
        data_ref = a.data_pagamento if a.data_pagamento else a.data_cadastro
        data_br = (data_ref - timedelta(hours=3)).date() if data_ref else hoje_brasil

        if data_br == hoje_brasil:
            label_data = f"{data_br.strftime('%d/%m/%Y')} (Hoje)"
        elif data_br == ontem_brasil:
            label_data = f"{data_br.strftime('%d/%m/%Y')} (Ontem)"
        else:
            label_data = data_br.strftime('%d/%m/%Y')

        if data_br not in pagos_agrupados:
            pagos_agrupados[data_br] = {'label': label_data, 'alunos': []}
        pagos_agrupados[data_br]['alunos'].append(a)

    lista_pagos_agrupados = [pagos_agrupados[k] for k in sorted(pagos_agrupados.keys(), reverse=True)]

    return render_template('clientes.html', alunos_pendentes=alunos_pendentes, pagos_agrupados=lista_pagos_agrupados, config=SiteSettings.query.first())

@app.route('/exportar_contatos')
@login_required
def exportar_contatos():
    if current_user.role == 'aluno': abort(403)
    todos_alunos = Aluno.query.filter_by(user_id=current_user.id).all()
    si = io.StringIO()
    cw = csv.writer(si, delimiter=';') 
    cw.writerow(['Nome do Aluno', 'WhatsApp', 'Curso / Disciplina', 'Status do Trabalho', 'Valor Cobrado (R$)', 'Data de Entrada'])
    for a in todos_alunos:
        data_str = a.data_cadastro.strftime('%d/%m/%Y') if a.data_cadastro else 'Não informada'
        valor_str = f"{a.valor:.2f}".replace('.', ',') if a.valor else '0,00'
        cw.writerow([a.nome, a.telefone, a.curso, a.status, valor_str, data_str])
        
    output = si.getvalue()
    return Response('\ufeff' + output, mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=Contatos_HubMaster.csv"})

@app.route('/mudar_status/<int:id>', methods=['POST'])
@login_required
def mudar_status(id):
    if current_user.role == 'aluno': abort(403)
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': abort(403)
    novo_status = request.form.get('novo_status')
    aluno.status = novo_status
    if novo_status == 'Pago':
        if not aluno.data_pagamento:
            aluno.data_pagamento = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('clientes'))

@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    if current_user.role == 'aluno': abort(403)
    config = SiteSettings.query.first()
    if request.method == 'POST':
        config.whatsapp_template = request.form.get('whatsapp_template')
        if current_user.role == 'admin':
            config.prompt_password = request.form.get('prompt_password')
            config.convert_api_key = request.form.get('convert_api_key')
            modelos_selecionados = request.form.getlist('modelos_ativos')
            novo_modelo = request.form.get('novo_modelo')
            if novo_modelo and novo_modelo.strip():
                if novo_modelo.strip() not in modelos_selecionados:
                    modelos_selecionados.append(novo_modelo.strip())
            config.modelos_ativos = ",".join(modelos_selecionados)
        db.session.commit()
        flash('Configurações salvas!', 'success')
        return redirect(url_for('configuracoes'))
        
    ativos_atuais = get_modelos_ativos()
    todos_para_exibir = list(TODOS_MODELOS_CONHECIDOS)
    for m in ativos_atuais:
        if m not in todos_para_exibir: todos_para_exibir.append(m)
    return render_template('configuracoes.html', config=config, todos_modelos=todos_para_exibir, modelos_ativos=ativos_atuais)

@app.route('/editar_cliente/<int:id>', methods=['POST'])
@login_required
def editar_cliente(id):
    if current_user.role == 'aluno': abort(403)
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': abort(403)
    aluno.nome, aluno.curso, aluno.telefone, aluno.ava_login, aluno.ava_senha = request.form.get('nome'), request.form.get('curso'), request.form.get('telefone'), request.form.get('ava_login'), request.form.get('ava_senha')
    try: aluno.valor = float(request.form.get('valor', '70.0').replace(',', '.'))
    except: pass
    db.session.commit(); flash('Dados atualizados!', 'success')
    return redirect(url_for('clientes'))

@app.route('/deletar_cliente/<int:id>', methods=['GET'])
@login_required
def deletar_cliente(id):
    if current_user.role == 'aluno': abort(403)
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': abort(403)
    db.session.delete(aluno); db.session.commit(); flash('Cliente apagado.', 'success')
    return redirect(url_for('clientes'))

@app.route('/cliente/<int:id>', methods=['GET'])
@login_required
def cliente_detalhe(id):
    if current_user.role == 'aluno': abort(403)
    aluno = Aluno.query.get_or_404(id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': abort(403)
    return render_template('cliente_detalhe.html', aluno=aluno)

@app.route('/adicionar_tema/<int:aluno_id>', methods=['POST'])
@login_required
def adicionar_tema(aluno_id):
    if current_user.role == 'aluno': abort(403)
    aluno = Aluno.query.get_or_404(aluno_id)
    if aluno.user_id != current_user.id and current_user.role != 'admin': abort(403)
    if request.form.get('texto'): 
        db.session.add(TemaTrabalho(aluno_id=aluno.id, titulo=request.form.get('titulo') or f"Tema {len(aluno.temas)+1}", texto=request.form.get('texto')))
        db.session.commit(); flash('Tema salvo!', 'success')
    return redirect(url_for('cliente_detalhe', id=aluno_id))

@app.route('/editar_tema/<int:tema_id>', methods=['POST'])
@login_required
def editar_tema(tema_id):
    if current_user.role == 'aluno': abort(403)
    tema = TemaTrabalho.query.get_or_404(tema_id)
    if Aluno.query.get(tema.aluno_id).user_id != current_user.id and current_user.role != 'admin': abort(403)
    tema.titulo, tema.texto = request.form.get('titulo'), request.form.get('texto')
    db.session.commit(); flash('Atualizado!', 'success')
    return redirect(url_for('cliente_detalhe', id=tema.aluno_id))

@app.route('/deletar_tema/<int:tema_id>')
@login_required
def deletar_tema(tema_id):
    if current_user.role == 'aluno': abort(403)
    tema = TemaTrabalho.query.get_or_404(tema_id)
    if Aluno.query.get(tema.aluno_id).user_id != current_user.id and current_user.role != 'admin': abort(403)
    db.session.delete(tema); db.session.commit(); flash('Apagado.', 'success')
    return redirect(url_for('cliente_detalhe', id=tema.aluno_id))

@app.route('/upload_doc/<int:aluno_id>', methods=['POST'])
@login_required
def upload_doc(aluno_id):
    if current_user.role == 'aluno': abort(403)
    try:
        arquivo = request.files.get('arquivo')
        if arquivo and arquivo.filename.lower().endswith(('.docx', '.pdf')):
            db.session.add(Documento(aluno_id=aluno_id, nome_arquivo=arquivo.filename, dados_arquivo=arquivo.read()))
            db.session.commit(); flash('Anexado!', 'success')
    except Exception as e: flash(f'Erro: {e}', 'error')
    return redirect(url_for('cliente_detalhe', id=aluno_id))

@app.route('/download_doc/<int:doc_id>')
def download_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    return send_file(io.BytesIO(doc.dados_arquivo), download_name=doc.nome_arquivo, as_attachment=True)

@app.route('/rename_doc/<int:doc_id>', methods=['POST'])
@login_required
def rename_doc(doc_id):
    if current_user.role == 'aluno': abort(403)
    doc = Documento.query.get_or_404(doc_id)
    novo_nome = request.form.get('novo_nome', '').strip()
    if novo_nome:
        ext = os.path.splitext(doc.nome_arquivo)[1]
        doc.nome_arquivo = novo_nome if novo_nome.lower().endswith(ext.lower()) else novo_nome + ext
        db.session.commit(); flash('Renomeado!', 'success')
    return redirect(url_for('cliente_detalhe', id=doc.aluno_id))

@app.route('/delete_doc/<int:doc_id>')
@login_required
def delete_doc(doc_id):
    if current_user.role == 'aluno': abort(403)
    doc = Documento.query.get_or_404(doc_id)
    aluno_id = doc.aluno_id
    db.session.delete(doc); db.session.commit()
    return redirect(url_for('cliente_detalhe', id=aluno_id))

@app.route('/prompts')
@login_required
def prompts():
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
    return render_template('prompts.html', prompts=PromptConfig.query.all())

@app.route('/prompts/action', methods=['POST'])
@login_required
def prompts_action():
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
    config = SiteSettings.query.first()
    if config and config.prompt_password and request.form.get('senha_master') != config.prompt_password:
        flash('Senha Master incorreta!', 'error')
        return redirect(url_for('prompts'))

    acao, prompt_id = request.form.get('acao'), request.form.get('prompt_id')
    if acao == 'add':
        db.session.add(PromptConfig(nome=request.form.get('nome'), texto=request.form.get('texto')))
        flash('Novo Cérebro criado!', 'success')
    elif acao == 'edit' and prompt_id:
        p = PromptConfig.query.get(prompt_id)
        if p: p.nome, p.texto = request.form.get('nome'), request.form.get('texto'); flash('Atualizado!', 'success')
    elif acao == 'delete' and prompt_id:
        p = PromptConfig.query.get(prompt_id)
        if p and p.is_default: flash('Não pode apagar o padrão base!', 'error')
        elif p: db.session.delete(p); flash('Apagado!', 'success')

    db.session.commit()
    return redirect(url_for('prompts'))

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
    if request.method == 'POST':
        data_exp = request.form.get('expiration_date')
        db.session.add(User(username=request.form.get('username'), password=generate_password_hash(request.form.get('password')), role=request.form.get('role'), creditos=0, expiration_date=datetime.strptime(data_exp, '%Y-%m-%d').date() if data_exp else None))
        db.session.commit(); flash('Usuário criado.', 'success')
    return render_template('admin.html', users=User.query.all(), hoje=date.today())

@app.route('/edit_user/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_user(id):
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
    user = User.query.get_or_404(id)
    if request.method == 'POST':
        if request.form.get('password'): user.password = generate_password_hash(request.form.get('password'))
        if current_user.role == 'admin': 
            user.role = request.form.get('role')
            if request.form.get('creditos') is not None:
                try: user.creditos = int(request.form.get('creditos'))
                except ValueError: pass
                
        data_exp = request.form.get('expiration_date')
        user.expiration_date = datetime.strptime(data_exp, '%Y-%m-%d').date() if data_exp else None
        db.session.commit(); flash('Usuário Atualizado!', 'success')
        return redirect(url_for('admin'))
    return render_template('edit_user.html', user=user)

@app.route('/delete_user/<int:id>')
@login_required
def delete_user(id):
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
    db.session.delete(User.query.get_or_404(id)); db.session.commit()
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True)
