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

# NOVA BIBLIOTECA DO GOOGLE
from google import genai 

app = Flask(__name__)

# =========================================================
# CONFIGURAÇÕES DO BANCO DE DADOS E SEGURANÇA
# =========================================================
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chave-super-secreta-mude-depois')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///clientes.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True, "pool_recycle": 300}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "Por favor, faça login para acessar."

# INICIALIZAÇÃO DO NOVO CLIENTE GEMINI
CHAVE_API = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=CHAVE_API) if CHAVE_API else None

# =========================================================
# MODELOS DO BANCO DE DADOS (CRM e Usuários)
# =========================================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='cliente') 
    expiration_date = db.Column(db.Date, nullable=True)
    alunos = db.relationship('Aluno', backref='responsavel', lazy=True)

class Aluno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    curso = db.Column(db.String(100))
    telefone = db.Column(db.String(20))
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)
    documentos = db.relationship('Documento', backref='aluno', lazy=True, cascade="all, delete-orphan")

class Documento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    dados_arquivo = db.Column(db.LargeBinary, nullable=False) 
    data_upload = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        hashed_pw = generate_password_hash('admin123')
        admin = User(username='admin', password=hashed_pw, role='admin')
        db.session.add(admin)
        db.session.commit()

# =========================================================
# FUNÇÕES DA IA E MANIPULAÇÃO DE WORD
# =========================================================
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

# PROMPT GLOBAL (Blindado e Rigoroso)
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
    (ATENÇÃO: É ESTRITAMENTE PROIBIDO COPIAR OU REPETIR AS INSTRUÇÕES ABAIXO DENTRO DA SUA RESPOSTA. FORNEÇA APENAS A SUA RESPOSTA FINAL DIRETAMENTE).
    
    [START_ASPECTO_1] [Resposta direta aqui] [END_ASPECTO_1]
    [START_POR_QUE_1] [Resposta direta aqui] [END_POR_QUE_1]
    [START_ASPECTO_2] [Resposta direta aqui] [END_ASPECTO_2]
    [START_POR_QUE_2] [Resposta direta aqui] [END_POR_QUE_2]
    [START_ASPECTO_3] [Resposta direta aqui] [END_ASPECTO_3]
    [START_POR_QUE_3] [Resposta direta aqui - OBRIGATÓRIO PREENCHER] [END_POR_QUE_3]
    [START_CONCEITOS_TEORICOS] - **[Nome]:** [Explicação]\n- **[Nome]:** [Explicação] [END_CONCEITOS_TEORICOS]
    [START_ANALISE_CONCEITO_1] [Sua análise direta] [END_ANALISE_CONCEITO_1]
    [START_ENTENDIMENTO_TEORICO] [Sua análise direta] [END_ENTENDIMENTO_TEORICO]
    [START_SOLUCOES_TEORICAS] [Seu plano direto] [END_SOLUCOES_TEORICAS]
    [START_RESUMO_MEMORIAL] [Resumo direto] [END_RESUMO_MEMORIAL]
    [START_CONTEXTO_MEMORIAL] [Contexto direto] [END_CONTEXTO_MEMORIAL]
    [START_ANALISE_MEMORIAL] [Análise final direta] [END_ANALISE_MEMORIAL]
    [START_PROPOSTAS_MEMORIAL] [Propostas diretas] [END_PROPOSTAS_MEMORIAL]
    [START_CONCLUSAO_MEMORIAL] [Conclusão direta] [END_CONCLUSAO_MEMORIAL]
    [START_REFERENCIAS_ADICIONAIS] [Referências ABNT diretas] [END_REFERENCIAS_ADICIONAIS]
    [START_AUTOAVALIACAO_MEMORIAL] [Autoavaliação direta] [END_AUTOAVALIACAO_MEMORIAL]
"""

def gerar_respostas_ia_tags(texto_tema, nome_modelo):
    prompt = f"TEMA/CASO DO DESAFIO:\n{texto_tema}\n\n{PROMPT_REGRAS_BASE}"
    try:
        # Nova sintaxe da biblioteca Google
        resposta = client.models.generate_content(model=nome_modelo, contents=prompt)
        return extrair_dicionario(resposta.text)
    except Exception as e:
        raise Exception(f"Falha na IA: {str(e)}")

def gerar_correcao_ia_tags(texto_tema, texto_trabalho, critica, nome_modelo):
    prompt = f"""Você é um aluno universitário corrigindo seu trabalho após feedback do professor.
    TEMA: {texto_tema}
    TRABALHO ATUAL: {texto_trabalho}
    CRÍTICA RECEBIDA: {critica}
    
    TAREFA: Reescreva as respostas aplicando as melhorias exigidas na crítica. 
    Lembre-se: Respeite rigorosamente o limite de caracteres e os limites exatos de parágrafos.
    {PROMPT_REGRAS_BASE}"""
    try:
        # Nova sintaxe
        resposta = client.models.generate_content(model=nome_modelo, contents=prompt)
        return extrair_dicionario(resposta.text)
    except Exception as e:
        raise Exception(f"Falha na IA (Correção): {str(e)}")

def extrair_dicionario(texto_ia):
    chaves = ["ASPECTO_1", "POR_QUE_1", "ASPECTO_2", "POR_QUE_2", "ASPECTO_3", "POR_QUE_3", "CONCEITOS_TEORICOS", "ANALISE_CONCEITO_1", "ENTENDIMENTO_TEORICO", "SOLUCOES_TEORICAS", "RESUMO_MEMORIAL", "CONTEXTO_MEMORIAL", "ANALISE_MEMORIAL", "PROPOSTAS_MEMORIAL", "CONCLUSAO_MEMORIAL", "REFERENCIAS_ADICIONAIS", "AUTOAVALIACAO_MEMORIAL"]
    dic = {}
    for chave in chaves:
        match = re.search(rf"\[START_{chave}\](.*?)\[END_{chave}\]", texto_ia, re.DOTALL)
        dic[f"{{{{{chave}}}}}"] = match.group(1).strip() if match else "" 
    return dic

# =========================================================
# ROTAS PRINCIPAIS DA FERRAMENTA E CRM
# =========================================================

# Modelos Hardcoded para evitar erro de listagem na nova API
MODELOS_DISPONIVEIS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash", "gemini-1.5-pro"]

@app.route('/')
@login_required
def index():
    if current_user.role == 'cliente' and current_user.expiration_date and date.today() > current_user.expiration_date:
        return render_template('expirado.html')
    alunos = Aluno.query.filter_by(user_id=current_user.id).all()
    return render_template('index.html', modelos=MODELOS_DISPONIVEIS, alunos=alunos)

@app.route('/processar', methods=['POST'])
@login_required
def processar():
    try:
        tema = request.form.get('tema')
        modelo = request.form.get('modelo')
        aluno_id = request.form.get('aluno_id')
        
        caminho_padrao = os.path.join(app.root_path, 'TEMPLATE_COM_TAGS.docx')
        with open(caminho_padrao, 'rb') as f: arquivo_memoria = io.BytesIO(f.read())

        respostas = gerar_respostas_ia_tags(tema, modelo)
        documento_pronto = preencher_template_com_tags(arquivo_memoria, respostas)
        arquivo_bytes = documento_pronto.read()
        arquivo_base64 = base64.b64encode(arquivo_bytes).decode('utf-8')
        
        if aluno_id:
            novo_doc = Documento(aluno_id=aluno_id, nome_arquivo=f"Trabalho_{datetime.now().strftime('%d%m%Y')}.docx", dados_arquivo=arquivo_bytes)
            db.session.add(novo_doc)
            db.session.commit()

        memorial_texto = f"### Resumo\n{respostas.get('{{RESUMO_MEMORIAL}}', '')}\n\n### Propostas\n{respostas.get('{{PROPOSTAS_MEMORIAL}}', '')}"
        
        return jsonify({
            "tipo": "sucesso_tags", "arquivo_base64": arquivo_base64, 
            "nome_arquivo": "Desafio_Preenchido.docx", "memorial_texto": memorial_texto,
            "dicionario_gerado": json.dumps(respostas, ensure_ascii=False)
        })
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# --- CRM: MEUS CLIENTES ---
@app.route('/clientes', methods=['GET', 'POST'])
@login_required
def clientes():
    if request.method == 'POST':
        novo_aluno = Aluno(
            user_id=current_user.id,
            nome=request.form.get('nome'),
            curso=request.form.get('curso'),
            telefone=request.form.get('telefone')
        )
        db.session.add(novo_aluno)
        db.session.commit()
        flash('Cliente cadastrado com sucesso!', 'success')
        return redirect(url_for('clientes'))
        
    alunos = Aluno.query.filter_by(user_id=current_user.id).all()
    return render_template('clientes.html', alunos=alunos)

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
    if not novo_nome.endswith('.docx'): novo_nome += '.docx'
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

# --- REVISÃO AVULSA ---
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
Faça uma crítica de 3 linhas apontando o que falta para tirar nota máxima (profundidade, conceitos, adequação). 
REGRA: Seja direto, NUNCA use formatações como negrito (**), itálico, bullet points ou títulos. Responda apenas com texto limpo."""
    try:
        resposta = client.models.generate_content(model=modelo, contents=prompt)
        critica_limpa = resposta.text.replace('*', '').replace('#', '').strip()
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
# ROTAS DE ADMINISTRAÇÃO E LOGIN (Padrão)
# =========================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        else: flash('Credenciais incorretas.', 'error')
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
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
    users = User.query.all()
    return render_template('admin.html', users=users, hoje=date.today())

@app.route('/edit_user/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_user(id):
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
    user = User.query.get_or_404(id)
    return render_template('edit_user.html', user=user)

@app.route('/delete_user/<int:id>')
@login_required
def delete_user(id):
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
    user = User.query.get_or_404(id)
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('admin'))

if __name__ == '__main__':
    app.run(debug=True)
