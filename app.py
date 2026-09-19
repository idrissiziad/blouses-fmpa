import streamlit as st
import pandas as pd
import psycopg2
import hmac
import hashlib
from datetime import datetime

# ================= CONFIGURATION & SECRETS =================
DATABASE_URL = st.secrets.get("DATABASE_URL", "")
SECRET_SALT = st.secrets.get("SECRET_SALT", "CLE_SECRET_SECURITE_TABLIERS_MAROC")
OPERATOR_PASSWORD = st.secrets.get("OPERATOR_PASSWORD", "BDE_STAFF_2026")
PRESIDENT_PASSWORD = st.secrets.get("PRESIDENT_PASSWORD", "PRESIDENT_ADMIN_2026")
# ===========================================================

st.set_page_config(page_title="Gestion Tabliers & Mesures", page_icon="🥼", layout="wide")

def get_connection():
    """Crée une connexion sécurisée vers Supabase (PostgreSQL)."""
    return psycopg2.connect(DATABASE_URL)

# Initialisation de la table PostgreSQL dans Supabase
def init_db():
    if not DATABASE_URL:
        st.error("DATABASE_URL non configurée dans les Secrets Streamlit.")
        st.stop()
    conn = get_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS tabliers (
            ticket_id TEXT PRIMARY KEY,
            date_creation TEXT,
            nom_prenom TEXT,
            type_commande TEXT,
            taille_standard TEXT,
            epaules TEXT,
            manche TEXT,
            poitrine TEXT,
            longueur TEXT,
            paiement TEXT,
            reste NUMERIC,
            code_secu TEXT,
            statut TEXT,
            date_remise TEXT
        )
    ''')
    conn.commit()
    c.close()
    conn.close()

init_db()

# Génération du code court HMAC pour le reçu papier
def generate_code(ticket_id: str, nom: str, info_taille: str) -> str:
    data = f"{ticket_id}|{nom.upper()}|{info_taille}|{SECRET_SALT}"
    sig = hmac.new(SECRET_SALT.encode(), data.encode(), hashlib.sha256).hexdigest()[:8].upper()
    return f"{sig[:4]}-{sig[4:]}"

def verify_code(ticket_id: str, nom: str, info_taille: str, code_saisi: str) -> bool:
    expected = generate_code(ticket_id, nom, info_taille)
    return hmac.compare_digest(expected, code_saisi.strip().upper())

# --- GESTION DES RÔLES & AUTHENTIFICATION ---
if "user_role" not in st.session_state:
    st.session_state["user_role"] = None

if not st.session_state["user_role"]:
    st.title("🔒 Accès Sécurisé - Gestion des Tabliers")
    pwd = st.text_input("Entrez votre mot de passe d'accès :", type="password")
    
    if st.button("Se connecter", type="primary"):
        if pwd == PRESIDENT_PASSWORD:
            st.session_state["user_role"] = "PRESIDENT"
            st.rerun()
        elif pwd == OPERATOR_PASSWORD:
            st.session_state["user_role"] = "OPERATOR"
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    st.stop()

# --- SIDEBAR (RÔLE & DÉCONNEXION) ---
with st.sidebar:
    if st.session_state["user_role"] == "PRESIDENT":
        st.success("👑 **Session : PRÉSIDENT (Admin)**")
        st.caption("Droits complets : Saisie, Remise, Modification & Réinitialisation.")
    else:
        st.info("👤 **Session : Opérateur BDE**")
        st.caption("Droits restreints : Saisie & Remise autorisées. Modifications bloquées.")
        
    if st.button("Se déconnecter"):
        st.session_state["user_role"] = None
        st.rerun()

# --- APPLICATION ---
st.title("🥼 Gestion, Mesures Tailleur & Distribution")

onglet1, onglet2, onglet3 = st.tabs([
    "📝 1. Enregistrement (Salle 1 & 2)", 
    "🔍 2. Vérification & Remise (Anti-fraude)", 
    "📊 3. Tableau Registre (Conforme Excel)"
])

# ================= ONGLET 1 : SAISIE =================
with onglet1:
    st.subheader("Nouvelle Commande / Prise de Mesure")
    
    col_gauche, col_droite = st.columns(2)
    
    with col_gauche:
        mode = st.radio("Circuit :", ["Salle 1 : Taille Standard (Express)", "Salle 2 : Prise de Mesure Tailleur (Sur-mesure)"])
        nom_prenom = st.text_input("Nom et Prénom :").strip().title()
        
        c_p1, c_p2 = st.columns(2)
        with c_p1:
            paiement = st.text_input("Paiement versé :", value="140 cash")
        with c_p2:
            reste = st.number_input("Reste à payer (DH) :", min_value=0, value=0, step=5)

    with col_droite:
        if "Salle 1" in mode:
            st.markdown("#### Choix Taille Standard")
            taille_standard = st.selectbox("Taille :", [
                "S", "M", "L", "XL", "XXL", 
                "S avec manche longue", "M avec manche longue", "L avec manche longue", "XL avec manche longue", 
                "Autre"
            ])
            epaules, manche, poitrine, longueur = "", "", "", ""
            type_cmd = "Standard"
            detail_cle = taille_standard
        else:
            st.markdown("#### 📏 Mesures Tailleur (en cm)")
            type_cmd = "Sur-mesure"
            taille_standard = "SUR-MESURE"
            c_m1, c_m2 = st.columns(2)
            with c_m1:
                epaules = st.text_input("Largeur des épaules (cm) :").strip()
                poitrine = st.text_input("Tour de Poitrine (cm) :").strip()
            with c_m2:
                manche = st.text_input("Longueur Manche (cm) :").strip()
                longueur = st.text_input("Longueur totale (cm) :").strip()
            detail_cle = f"{epaules}-{manche}-{poitrine}-{longueur}"

    if st.button("Enregistrer et Générer le Reçu", type="primary"):
        if not nom_prenom:
            st.warning("Veuillez renseigner le Nom et Prénom.")
        elif type_cmd == "Sur-mesure" and not (epaules and manche and poitrine and longueur):
            st.warning("Veuillez renseigner toutes les mesures du tailleur.")
        else:
            conn = get_connection()
            c = conn.cursor()
            prefix = "S1" if type_cmd == "Standard" else "S2"
            c.execute("SELECT COUNT(*) FROM tabliers WHERE ticket_id LIKE %s", (f"{prefix}%",))
            num = c.fetchone()[0] + 1
            ticket_id = f"{prefix}-{num:03d}"
            
            code_secu = generate_code(ticket_id, nom_prenom, detail_cle)
            date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            c.execute('''
                INSERT INTO tabliers VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (ticket_id, date_now, nom_prenom, type_cmd, taille_standard, epaules, manche, poitrine, longueur, paiement, reste, code_secu, "Non Confirmé", "En attente"))
            conn.commit()
            c.close()
            conn.close()
            
            st.success(f"Commande validée pour {nom_prenom} !")
            st.markdown(f"""
            ---
            ### 🧾 INFORMATIONS À ÉCRIRE SUR LE REÇU PAPIER :
            - **N° Ticket :** `{ticket_id}`
            - **Nom :** `{nom_prenom}`
            - **Taille / Type :** `{"Standard (" + taille_standard + ")" if type_cmd == "Standard" else "Sur-mesure"}`
            - **Reste à payer :** `{reste} DH`
            - **Code de Sécurité Anti-fraude :** `{code_secu}`
            ---
            """)

# ================= ONGLET 2 : VÉRIFICATION & REMISE =================
with onglet2:
    st.subheader("Distribution du Tablier (Contrôle Reçu Papier)")
    
    v1, v2 = st.columns(2)
    with v1:
        s_id = st.text_input("N° Ticket inscrit sur le papier (ex: S1-005 ou S2-002) :").strip().upper()
    with v2:
        s_code = st.text_input("Code Sécurité inscrit (ex: 8F2A-4B9C) :").strip().upper()
        
    if st.button("Vérifier le Ticket", type="secondary"):
        if not s_id or not s_code:
            st.warning("Veuillez entrer le numéro de ticket ET le code.")
        else:
            conn = get_connection()
            c = conn.cursor()
            c.execute("SELECT * FROM tabliers WHERE ticket_id = %s", (s_id,))
            row = c.fetchone()
            c.close()
            conn.close()
            
            if not row:
                st.error("❌ Ticket inconnu dans la base de données !")
            else:
                (t_id, t_date_c, t_nom, t_type, t_taille, t_ep, t_ma, t_po, t_lo, t_paie, t_reste, t_code, t_statut, t_date_r) = row
                
                info_cle = t_taille if t_type == "Standard" else f"{t_ep}-{t_ma}-{t_po}-{t_lo}"
                
                if not verify_code(t_id, t_nom, info_cle, s_code):
                    st.error("🚨 ALERTE FALSIFICATION : Le code ne correspond pas à ce ticket ou le nom/la taille a été falsifié !")
                else:
                    if t_statut == "Confirmé":
                        st.error(f"""
                        ⛔ **TABLIER DÉJÀ REMIS !**
                        - **Bénéficiaire :** {t_nom}
                        - **Remis le :** {t_date_r}
                        """)
                    else:
                        st.success("✅ **TICKET AUTHENTIQUE - EN ATTENTE DE REMISE**")
                        st.write(f"**Étudiant(e) :** {t_nom}")
                        
                        if float(t_reste) > 0:
                            st.warning(f"⚠️ **ATTENTION CAISSE :** Reste à régler : **{t_reste} DH** avant remise.")
                        else:
                            st.info("💰 Paiement complet déjà effectué (Reste: 0 DH).")
                            
                        if t_type == "Standard":
                            st.markdown(f"### 🥼 Taille à donner : **{t_taille}**")
                        else:
                            st.markdown("### 🥼 Mesures Spécifiques du Tailleur :")
                            st.write(f"- Épaules : **{t_ep} cm** | Manche : **{t_ma} cm** | Poitrine : **{t_po} cm** | Longueur : **{t_lo} cm**")
                            
                        st.session_state["ticket_a_valider"] = t_id

    # Validation finale
    if "ticket_a_valider" in st.session_state and st.session_state["ticket_a_valider"] == s_id:
        st.markdown("---")
        if st.button("📦 Confirmer la Remise Définitive du Tablier", type="primary"):
            conn = get_connection()
            c = conn.cursor()
            date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            c.execute("UPDATE tabliers SET statut = 'Confirmé', date_remise = %s, reste = 0 WHERE ticket_id = %s", (date_now, s_id))
            conn.commit()
            c.close()
            conn.close()
            st.balloons()
            st.success(f"Tablier {s_id} marqué comme REMIS (Reste soldé).")
            del st.session_state["ticket_a_valider"]

# ================= ONGLET 3 : REGISTRE & GESTION PRÉSIDENT =================
with onglet3:
    st.subheader("Registre Conforme à votre Feuille de Suivi")
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM tabliers ORDER BY date_creation DESC", conn)
    conn.close()
    
    if not df.empty:
        total = len(df)
        confirmes = len(df[df["statut"] == "Confirmé"])
        restes_dus = df[df["reste"] > 0]["reste"].sum()
        
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Tabliers", total)
        m2.metric("Tabliers Remis", f"{confirmes} / {total}")
        m3.metric("Reste total à encaisser", f"{restes_dus:.0f} DH")
        
        df_display = df[[
            "ticket_id", "nom_prenom", "paiement", "reste", 
            "taille_standard", "epaules", "manche", "poitrine", "longueur", "statut", "date_remise"
        ]].copy()
        
        df_display.columns = [
            "Ticket", "Nom et Prénom", "Paiement", "Reste (DH)", 
            "Taille Standard", "Largeur épaules", "MANCHE", "POITRINE", "Longueur", "Statut", "Date Remise"
        ]
        
        def highlight_status(val):
            if val == 'Confirmé':
                return 'background-color: #d4edda; color: #155724; font-weight: bold;'
            return 'background-color: #fff3cd; color: #856404;'

        styler = df_display.style
        style_method = getattr(styler, "map", None) or getattr(styler, "applymap")
        st.dataframe(style_method(highlight_status, subset=['Statut']), use_container_width=True)
        
        csv_data = df_display.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Télécharger le tableau (Excel/CSV)", data=csv_data, file_name="tabliers_commandes.csv", mime="text/csv")
    else:
        st.info("Aucune commande enregistrée.")

    # ================= CONTRÔLE STRICT RÉSERVÉ AU PRÉSIDENT =================
    st.markdown("---")
    if st.session_state["user_role"] == "PRESIDENT":
        with st.expander("👑 Administration Président (Modification & Suppression)"):
            st.warning("⚠️ Seul le Président peut modifier ou purger les enregistrements.")
            
            tab_del, tab_reset = st.tabs(["Supprimer un Ticket", "Réinitialiser Tout le Registre"])
            
            with tab_del:
                c_del1, c_del2 = st.columns([3, 1])
                with c_del1:
                    t_del = st.text_input("N° Ticket à supprimer (ex: S1-002) :").strip().upper()
                with c_del2:
                    st.write("")
                    st.write("")
                    if st.button("Supprimer", type="secondary"):
                        if t_del:
                            conn = get_connection()
                            c = conn.cursor()
                            c.execute("DELETE FROM tabliers WHERE ticket_id = %s", (t_del,))
                            conn.commit()
                            c.close()
                            conn.close()
                            st.success(f"Ticket {t_del} supprimé de Supabase.")
                            st.rerun()
            
            with tab_reset:
                st.error("Cette opération supprime toutes les données dans Supabase.")
                confirm_wipe = st.checkbox("Je confirme vouloir purger complètement le registre.")
                if st.button("🗑️ Vider définitivement la base", type="primary"):
                    if confirm_wipe:
                        conn = get_connection()
                        c = conn.cursor()
                        c.execute("TRUNCATE TABLE tabliers")
                        conn.commit()
                        c.close()
                        conn.close()
                        st.success("Toutes les données ont été effacées.")
                        st.rerun()
                    else:
                        st.warning("Veuillez d'abord cocher la case de confirmation.")
    else:
        st.caption("🔒 *Les fonctionnalités de suppression et modification du registre sont strictement réservées au Président.*")
