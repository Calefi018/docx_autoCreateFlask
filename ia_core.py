import re
import json
import requests
import hashlib
from google import genai

def limpar_texto_ia(texto):
    try: 
        texto = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), texto)
    except Exception: 
        pass
        
    texto = re.sub(r'(?<!\*)\*(?!\*)', '', texto)
    
    substituicoes = {
        r'\bmomentum\b': 'impulso',
        r'\blocus\b': 'ambiente',
        r'\boutrossim\b': 'além disso',
        r'\bdessarte\b': 'assim',
        r'\bdestarte\b': 'assim',
        r'\bum mergulho profundo\b': 'uma análise detalhada',
        r'\bmergulho profundo\b': 'análise detalhada',
        r'\btapeçaria de\b': 'conjunto de',
        r'\btapeçaria\b': 'estrutura',
        r'\bfarol\b': 'guia',
        r'\bcrucial\b': 'essencial',
        r'\bvital\b': 'essencial',
        r'\badentrar\b': 'explorar',
        r'\btestamento\b': 'prova',
        r'\bpaisagem\b': 'cenário',
        r'\bnotável\b': 'importante'
    }
    for padrao, substituto in substituicoes.items():
        texto = re.sub(padrao, substituto, texto, flags=re.IGNORECASE)
    return texto

def calcular_custo_api(modelo, prompt_tokens, completion_tokens):
    usd_to_brl = 5.50
    custo_usd = 0.0
    mod_lower = modelo.lower()
    
    if "claude-3.5-sonnet" in mod_lower: custo_usd = (prompt_tokens / 1000000 * 3.0) + (completion_tokens / 1000000 * 15.0)
    elif "claude-3-opus" in mod_lower: custo_usd = (prompt_tokens / 1000000 * 15.0) + (completion_tokens / 1000000 * 75.0)
    elif "gpt-4o-mini" in mod_lower: custo_usd = (prompt_tokens / 1000000 * 0.15) + (completion_tokens / 1000000 * 0.60)
    elif "gpt-4o" in mod_lower: custo_usd = (prompt_tokens / 1000000 * 2.5) + (completion_tokens / 1000000 * 10.0)
    elif "llama-3.3-70b" in mod_lower: custo_usd = (prompt_tokens / 1000000 * 0.4) + (completion_tokens / 1000000 * 0.4)
    elif "qwen" in mod_lower: custo_usd = (prompt_tokens / 1000000 * 0.4) + (completion_tokens / 1000000 * 0.4)
    elif "gemini-2.5-pro" in mod_lower or "gemini-pro" in mod_lower: custo_usd = (prompt_tokens / 1000000 * 1.25) + (completion_tokens / 1000000 * 5.0)
    elif "gemini-2.5-flash" in mod_lower or "gemini-flash" in mod_lower: custo_usd = (prompt_tokens / 1000000 * 0.075) + (completion_tokens / 1000000 * 0.3)
        
    return custo_usd * usd_to_brl

def chamar_ia(prompt, nome_modelo, chave_google=None, chave_openrouter=None):
    is_openrouter = "openrouter/" in nome_modelo.lower() or "/" in nome_modelo
    custo_reais = 0.0
    
    if is_openrouter:
        if not chave_openrouter: raise Exception("A Chave da API do OpenRouter não foi configurada.")
        modelo_limpo = nome_modelo.replace("openrouter/", "")
        headers = {"Authorization": f"Bearer {chave_openrouter}", "HTTP-Referer": "https://hubmaster-system.com", "Content-Type": "application/json"}
        payload = {"model": modelo_limpo, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7}
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=180)
        if res.status_code != 200: raise Exception(f"Erro OpenRouter ({res.status_code}): {res.text}")
            
        dados = res.json()
        texto = dados['choices'][0]['message']['content']
        usage = dados.get('usage', {})
        custo_reais = calcular_custo_api(modelo_limpo, usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0))
        return limpar_texto_ia(texto), custo_reais
    else:
        if not chave_google: raise Exception("A Chave da API nativa do Google não foi configurada.")
        client = genai.Client(api_key=chave_google)
        res = client.models.generate_content(model=nome_modelo, contents=prompt)
        try:
            pt = res.usage_metadata.prompt_token_count
            ct = res.usage_metadata.candidates_token_count
            custo_reais = calcular_custo_api(nome_modelo, pt, ct)
        except Exception: pass
        return limpar_texto_ia(res.text), custo_reais

def extrair_dicionario(texto_ia):
    chaves = [
        "ASPECTO_1", "POR_QUE_1", "ASPECTO_2", "POR_QUE_2", "ASPECTO_3", "POR_QUE_3", 
        "CONCEITOS_TEORICOS", "ANALISE_CONCEITO_1", "ENTENDIMENTO_TEORICO", "SOLUCOES_TEORICAS", 
        "RESUMO_MEMORIAL", "CONTEXTO_MEMORIAL", "ANALISE_MEMORIAL", "PROPOSTAS_MEMORIAL", 
        "CONCLUSAO_MEMORIAL", "REFERENCIAS_ADICIONAIS", "AUTOAVALIACAO_MEMORIAL"
    ]
    dic = {}
    for chave in chaves:
        match = re.search(rf"\[START_{chave}\](.*?)(?=\[END_{chave}\]|\[START_|$)", texto_ia, re.DOTALL | re.IGNORECASE)
        if match:
            trecho = match.group(1).strip()
            while trecho.startswith('**') and trecho.endswith('**') and len(trecho) > 4: trecho = trecho[2:-2].strip()
            dic[f"{{{{{chave}}}}}"] = trecho
        else: 
            dic[f"{{{{{chave}}}}}"] = "" 
    return dic

def extrair_json_seguro(texto):
    try:
        match = re.search(r'\[.*\]', texto, re.DOTALL)
        if match: return json.loads(match.group(0))
        return json.loads(texto)
    except Exception: return []

def consultar_saldo_openrouter(chave_openrouter):
    try:
        if not chave_openrouter: return 0.0
        headers = {"Authorization": f"Bearer {chave_openrouter}"}
        res = requests.get("https://openrouter.ai/api/v1/credits", headers=headers, timeout=10)
        if res.status_code == 200:
            dados = res.json().get("data", {})
            return max((float(dados.get("total_credits", 0.0)) - float(dados.get("total_usage", 0.0))), 0.0)
        return 0.0
    except Exception: return 0.0

# =========================================================
# MENTE COLETIVA 2.0: MOTORES DE FATIAMENTO E COMPARAÇÃO
# =========================================================
def fatiar_prova(texto_prova):
    """Fatia a prova de forma lógica. Se o texto for puro lixo, retorna vazio para a IA limpar."""
    partes = re.split(r'(?mi)^(?:quest[ãa]o|pergunta)?\s*(?:n?[oº]?\s*)?(\d+)[\)\-\.\s:]\s+', texto_prova)
    if len(partes) <= 1:
        partes = re.split(r'(?mi)^\s*(\d+)\s*$', texto_prova)
        
    questoes = []
    if len(partes) > 1:
        for i in range(1, len(partes), 2):
            try:
                num = int(partes[i].strip())
                corpo = partes[i+1].strip()
                
                enunciado_match = re.split(r'(?m)^\s*([a-eA-E])[\)\-\.]', corpo)
                enunciado = enunciado_match[0].strip() if enunciado_match else corpo.strip()
                
                alternativas = {}
                alt_raw = re.findall(r'(?m)^\s*([a-eA-E])[\)\-\.]\s*(.*?)(?=^\s*[a-eA-E][\)\-\.]|\Z)', corpo, re.DOTALL)
                
                if not alt_raw:
                    alt_raw = re.findall(r'\b([a-eA-E])[\)\-\.]\s*(.*?)(?=\b[a-eA-E][\)\-\.]|\Z)', corpo, re.DOTALL)
                    
                for letra, texto_alt in alt_raw:
                    alternativas[letra.upper()] = texto_alt.strip()
                    
                questoes.append({
                    'numero': num,
                    'enunciado': enunciado,
                    'alternativas': alternativas,
                    'texto_original': f"Questão {num}\n{corpo}"
                })
            except:
                continue
    return questoes

def fatiar_prova_com_ia(texto_prova, chave_google, chave_openrouter):
    """PADRÃO OURO: O modelo Gemini organiza provas ilegíveis antes da lógica principal."""
    prompt = f"""Sua única função é ler o texto bruto de uma prova caótica e convertê-lo para um Array JSON puro.
Ignore introduções, rodapés e textos irrelevantes.
Se as alternativas não tiverem letras (A, B, C), atribua automaticamente na ordem.
ESTRUTURA EXATA OBRIGATÓRIA (SÓ RETORNE O ARRAY JSON):
[
  {{
    "numero": 1,
    "enunciado": "Texto da pergunta aqui...",
    "alternativas": {{ "A": "Opção 1", "B": "Opção 2", "C": "Opção 3", "D": "Opção 4" }},
    "texto_original": "Questão 1\\nTexto da pergunta...\\nA) Opção 1"
  }}
]
TEXTO BRUTO DA PROVA:
{texto_prova}"""
    
    resposta, custo = chamar_ia(prompt, "google/gemini-2.5-flash", chave_google, chave_openrouter)
    dados_json = extrair_json_seguro(resposta)
    
    questoes_limpas = []
    for q in dados_json:
        try:
            q['numero'] = int(q.get('numero', 0))
            if 'alternativas' not in q: q['alternativas'] = {}
            if 'enunciado' not in q: q['enunciado'] = ''
            if 'texto_original' not in q: q['texto_original'] = f"Questão {q['numero']}\n{q['enunciado']}"
            questoes_limpas.append(q)
        except:
            pass
            
    return questoes_limpas, custo

def gerar_hash_enunciado(enunciado):
    limpo = re.sub(r'[\W_0-9]+', '', str(enunciado).lower().strip())
    return hashlib.md5(limpo[:300].encode('utf-8')).hexdigest()

def encontrar_letra_por_texto(texto_correto, alternativas_prova):
    if not texto_correto or not alternativas_prova: return None
    texto_correto_limpo = re.sub(r'[\W_]+', '', str(texto_correto).lower().strip())
    for letra, texto_alt in alternativas_prova.items():
        alt_limpa = re.sub(r'[\W_]+', '', str(texto_alt).lower().strip())
        if texto_correto_limpo in alt_limpa or alt_limpa in texto_correto_limpo:
            return letra.upper()
    return None