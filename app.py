import streamlit as st
import pandas as pd
import psycopg2
import hmac
import hashlib
from datetime import datetime

# ================= CONFIGURATION & SECRETS =================
DATABASE_URL = st.secrets.get("DATABASE_URL", "")
SECRET_SALT = st.secrets.get("SECRET_SALT", "CLE_SECRET_SECURITE_TABLIERS_MAROC")
PRESIDENT_PASSWORD = st.secrets.get("PRESIDENT_PASSWORD", "")
BDE_ACCOUNTS = st.secrets.get("bde_accounts", {})
OPERATOR_PASSWORD = st.secrets.get("OPERATOR_PASSWORD", "")  # Rétrocompatibilité
# ===========================================================

st.set_page_config(page_title="Gestion Tabliers & Mesures", page_icon="🥼", layout="wide")

def get_connection():
    """Crée une connexion sécurisée vers Supabase (PostgreSQL)."""
    return psycopg2.connect(DATABASE_URL)

# Initialisation et migration de la table PostgreSQL dans Supabase
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
            date_remise TEXT,
            cree_par TEXT,
            valide_par TEXT
        )
    ''')
    # Migration automatique non-destructive si la table existait déjà
    c.execute("ALTER TABLE tabliers ADD COLUMN IF NOT EXISTS cree_par TEXT;")
    c.execute("ALTER TABLE tabliers ADD COLUMN IF NOT EXISTS valide_par TEXT;")
    conn.commit()
    c.close()
    conn.close()

init_db()

# Génération du code court HMAC pour le reçu papier
def generate_code(ticket_id: str, nom: str, info_taille: str) -> str:
    data = f"{ticket_id}|{nom.upper()}|{info_taille}|{SECRET_SALT}"
    return hmac.new(SECRET_SALT.encode(), data.encode(), hashlib.sha256).hexdigest()[:4].upper()

def verify_code(ticket_id: str, nom: str, info_taille: str, code_saisi: str) -> bool:
    expected = generate_code(ticket_id, nom, info_taille)
    return hmac.compare_digest(expected, code_saisi.strip().upper())

# Génération sécurisée du prochain numéro de ticket (évite les doublons et gère les suppressions)
def get_next_ticket_id(c, prefix: str) -> str:
    """Trouve le prochain numéro disponible basé sur le MAX existant."""
    c.execute("""
        SELECT COALESCE(MAX(CAST(SPLIT_PART(ticket_id, '-', 2) AS INTEGER)), 0) + 1
        FROM tabliers
        WHERE ticket_id ~ %s
    """, (f"^{prefix}-[0-9]+$",))
    row = c.fetchone()
    num = row[0] if row and row[0] else 1
    
    # Sécurité anti-collision supplémentaire
    while True:
        candidate_id = f"{prefix}-{num:03d}"
        c.execute("SELECT 1 FROM tabliers WHERE ticket_id = %s", (candidate_id,))
        if not c.fetchone():
            return candidate_id
        num += 1

# --- GESTION DES RÔLES & AUTHENTIFICATION NOM + MOT DE PASSE ---
if "user_role" not in st.session_state:
    st.session_state["user_role"] = None
if "user_name" not in st.session_state:
    st.session_state["user_name"] = None

if not st.session_state["user_role"]:
    st.title("🔒 Connexion BDE - Gestion des Tabliers")
    col_auth, _ = st.columns([1, 1])
    with col_auth:
        login_nom = st.text_input("Identifiant / Nom BDE :").strip()
        pwd = st.text_input("Mot de passe :", type="password")
        
        if st.button("Se connecter", type="primary"):
            if not login_nom or not pwd:
                st.warning("Veuillez saisir votre Nom BDE et votre Mot de passe.")
            elif pwd == PRESIDENT_PASSWORD:
                st.session_state["user_role"] = "PRESIDENT"
                st.session_state["user_name"] = login_nom if login_nom.lower() != "president" else "Président"
                st.rerun()
            else:
                # Vérification insensible à la casse dans BDE_ACCOUNTS
                accounts_map = {str(k).strip().lower(): (str(k), str(v)) for k, v in BDE_ACCOUNTS.items()}
                nom_key = login_nom.lower()
                
                if nom_key in accounts_map and accounts_map[nom_key][1] == pwd:
                    st.session_state["user_role"] = "OPERATOR"
                    st.session_state["user_name"] = accounts_map[nom_key][0]
                    st.rerun()
                elif OPERATOR_PASSWORD and pwd == OPERATOR_PASSWORD:
                    # Repli si l'ancien mot de passe unique opérateur est utilisé
                    st.session_state["user_role"] = "OPERATOR"
                    st.session_state["user_name"] = login_nom
                    st.rerun()
                else:
                    st.error("Identifiant ou mot de passe incorrect.")
    st.stop()

# --- SIDEBAR (UTILISATEUR CONNECTÉ & DÉCONNEXION) ---
with st.sidebar:
    nom_connecte = st.session_state["user_name"]
    if st.session_state["user_role"] == "PRESIDENT":
        st.success(f"👑 **Session : {nom_connecte} (Président)**")
        st.caption("Droits complets : Saisie, Remise, Modification & Réinitialisation.")
    else:
        st.info(f"👤 **Session : {nom_connecte} (Opérateur BDE)**")
        st.caption("Droits : Saisie & Remise. Vos actions sont enregistrées sous votre nom.")
        
    if st.button("Se déconnecter"):
        st.session_state["user_role"] = None
        st.session_state["user_name"] = None
        st.session_state.pop("verified_ticket", None)
        st.rerun()

# --- APPLICATION PRINCIPALE ---
st.title("🥼 Gestion, Mesures Tailleur & Distribution")

onglet1, onglet2, onglet3 = st.tabs([
    "📝 1. Enregistrement (Salle 1 & 2)", 
    "🔍 2. Vérification & Prise de Mesures / Remise", 
    "📊 3. Tableau Registre (Conforme Excel)"
])

# ================= ONGLET 1 : SAISIE =================
with onglet1:
    st.subheader("Nouvelle Commande / Prise de Mesure")
    
    col_gauche, col_droite = st.columns(2)
    
    with col_gauche:
        mode = st.radio("Circuit :", [
            "Salle 1 : Taille Standard (Express - Remise Immédiate)", 
            "Salle 2 : Sur-Mesure Tailleur (Mesures prises en Salle 2)"
        ])
        nom_prenom = st.text_input("Nom et Prénom de l'étudiant :").strip().title()
        
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
            st.success("⚡ **Remise immédiate :** Ce tablier sera automatiquement marqué comme **Confirmé / Remis**.")
        else:
            st.markdown("#### 📏 Prise de Mesure Tailleur (Salle 2)")
            st.info("ℹ️ Les mesures tailleur seront saisies dans l'**Onglet 2** une fois le ticket vérifié.")
            type_cmd = "Sur-mesure"
            taille_standard = "SUR-MESURE"
            epaules, manche, poitrine, longueur = "", "", "", ""
            detail_cle = "SUR-MESURE"

    if st.button("Enregistrer et Générer le Reçu", type="primary"):
        if not nom_prenom:
            st.warning("Veuillez renseigner le Nom et Prénom de l'étudiant.")
        else:
            conn = get_connection()
            c = conn.cursor()
            try:
                prefix = "S1" if type_cmd == "Standard" else "S2"
                ticket_id = get_next_ticket_id(c, prefix)
                
                code_secu = generate_code(ticket_id, nom_prenom, detail_cle)
                date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                operateur_actuel = st.session_state["user_name"]
                
                # En Salle 1, c'est directement validé par cet opérateur
                if type_cmd == "Standard":
                    statut_init = "Confirmé"
                    date_remise_init = date_now
                    valide_par_init = operateur_actuel
                else:
                    statut_init = "Non Confirmé"
                    date_remise_init = "En attente mesures"
                    valide_par_init = None
                
                c.execute('''
                    INSERT INTO tabliers (
                        ticket_id, date_creation, nom_prenom, type_commande, taille_standard,
                        epaules, manche, poitrine, longueur, paiement, reste, code_secu,
                        statut, date_remise, cree_par, valide_par
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', (
                    ticket_id, date_now, nom_prenom, type_cmd, taille_standard, 
                    epaules, manche, poitrine, longueur, paiement, reste, code_secu, 
                    statut_init, date_remise_init, operateur_actuel, valide_par_init
                ))
                conn.commit()
                
                if type_cmd == "Standard":
                    st.success(f"Tablier remis et validé par **{operateur_actuel}** pour {nom_prenom} !")
                else:
                    st.success(f"Ticket généré par **{operateur_actuel}** pour {nom_prenom} !")

                st.markdown(f"""
                ---
                ### 🧾 INFORMATIONS DU REÇU PAPIER :
                - **N° Ticket :** `{ticket_id}`
                - **Nom :** `{nom_prenom}`
                - **Circuit :** `{"Salle 1 - Standard (" + taille_standard + ")" if type_cmd == "Standard" else "Salle 2 - Sur-Mesure"}`
                - **Reste à payer :** `{reste} DH`
                - **Code de Sécurité :** `{code_secu}`
                - **Enregistré par :** `{operateur_actuel}`
                - **Statut :** `{statut_init}`
                ---
                """)
            except psycopg2.IntegrityError:
                conn.rollback()
                st.error("Un conflit de numéro de ticket est survenu. Veuillez cliquer à nouveau pour réessayer.")
            except Exception as e:
                conn.rollback()
                st.error(f"Une erreur est survenue : {e}")
            finally:
                c.close()
                conn.close()

# ================= ONGLET 2 : VÉRIFICATION & PRISE DE MESURES =================
with onglet2:
    st.subheader("Distribution & Saisie Mesures Tailleur")
    
    if "verified_ticket" not in st.session_state:
        st.session_state["verified_ticket"] = None

    v1, v2, v3 = st.columns([2, 2, 1])
    with v1:
        s_id = st.text_input("N° Ticket (ex: S1-001 ou S2-001) :").strip().upper()
    with v2:
        s_code = st.text_input("Code Sécurité Reçu (ex: 8F2A) :").strip().upper()
    with v3:
        st.write("")
        st.write("")
        if st.button("Vérifier le Ticket", type="secondary"):
            if not s_id or not s_code:
                st.warning("Remplissez le numéro et le code.")
                st.session_state["verified_ticket"] = None
            else:
                conn = get_connection()
                c = conn.cursor()
                c.execute("""
                    SELECT ticket_id, date_creation, nom_prenom, type_commande, taille_standard, 
                           epaules, manche, poitrine, longueur, paiement, reste, code_secu, 
                           statut, date_remise, cree_par, valide_par 
                    FROM tabliers WHERE ticket_id = %s
                """, (s_id,))
                row = c.fetchone()
                c.close()
                conn.close()
                
                if not row:
                    st.error("❌ Ticket introuvable dans la base de données !")
                    st.session_state["verified_ticket"] = None
                else:
                    (t_id, t_date_c, t_nom, t_type, t_taille, t_ep, t_ma, t_po, t_lo, t_paie, t_reste, t_code, t_statut, t_date_r, t_cree, t_valide) = row
                    info_cle = t_taille if t_type == "Standard" else "SUR-MESURE"
                    
                    if not verify_code(t_id, t_nom, info_cle, s_code):
                        st.error("🚨 ALERTE : Le code ne correspond pas à ce ticket ou nom !")
                        st.session_state["verified_ticket"] = None
                    else:
                        st.session_state["verified_ticket"] = row

    # --- TRAITEMENT DU TICKET APRÈS VALIDATION DU CODE ---
    if st.session_state["verified_ticket"]:
        (t_id, t_date_c, t_nom, t_type, t_taille, t_ep, t_ma, t_po, t_lo, t_paie, t_reste, t_code, t_statut, t_date_r, t_cree, t_valide) = st.session_state["verified_ticket"]
        
        st.markdown("---")
        st.success(f"✅ **Ticket Authentifié :** `{t_id}` | **Étudiant :** `{t_nom}` | **Créé par :** `{t_cree or 'Inconnu'}`")
        
        if float(t_reste) > 0:
            st.warning(f"⚠️ **ATTENTION CAISSE :** Reste à régler : **{t_reste} DH** avant validation.")
        else:
            st.info("💰 Solde à jour (0 DH restant).")

        # Cas 1 : Sur-mesure (Salle 2) -> Saisie des mesures tailleur
        if t_type == "Sur-mesure":
            if t_statut == "Confirmé":
                st.warning(f"⚠️ Mesures déjà validées par **{t_valide or 'Inconnu'}** le {t_date_r}.")
                st.write(f"Mesures actuelles : Épaules: **{t_ep} cm**, Manche: **{t_ma} cm**, Poitrine: **{t_po} cm**, Longueur: **{t_lo} cm**")
            
            st.markdown("### 📏 Saisie des Mesures Tailleur (Salle 2)")
            c_m1, c_m2 = st.columns(2)
            with c_m1:
                in_ep = st.text_input("Largeur des épaules (cm) :", value=t_ep or "").strip()
                in_po = st.text_input("Tour de Poitrine (cm) :", value=t_po or "").strip()
            with c_m2:
                in_ma = st.text_input("Longueur Manche (cm) :", value=t_ma or "").strip()
                in_lo = st.text_input("Longueur totale (cm) :", value=t_lo or "").strip()

            if st.button("💾 Enregistrer les Mesures & Confirmer la Remise", type="primary"):
                if not (in_ep and in_ma and in_po and in_lo):
                    st.error("Veuillez renseigner toutes les 4 mesures avant de confirmer.")
                else:
                    conn = get_connection()
                    c = conn.cursor()
                    date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    c.execute('''
                        UPDATE tabliers 
                        SET epaules = %s, manche = %s, poitrine = %s, longueur = %s, statut = 'Confirmé', date_remise = %s, reste = 0, valide_par = %s 
                        WHERE ticket_id = %s
                    ''', (in_ep, in_ma, in_po, in_lo, date_now, st.session_state["user_name"], t_id))
                    conn.commit()
                    c.close()
                    conn.close()
                    st.balloons()
                    st.success(f"Mesures enregistrées et confirmées par {st.session_state['user_name']} !")
                    st.session_state["verified_ticket"] = None

        # Cas 2 : Standard (Salle 1)
        else:
            if t_statut == "Confirmé":
                st.info(f"ℹ️ Tablier Standard déjà validé et remis par **{t_valide or 'Inconnu'}** le {t_date_r} (Taille : **{t_taille}**).")
            else:
                st.markdown(f"### 🥼 Taille à remettre : **{t_taille}**")
                if st.button("📦 Confirmer la Remise Définitive", type="primary"):
                    conn = get_connection()
                    c = conn.cursor()
                    date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    c.execute("""
                        UPDATE tabliers 
                        SET statut = 'Confirmé', date_remise = %s, reste = 0, valide_par = %s 
                        WHERE ticket_id = %s
                    """, (date_now, st.session_state["user_name"], t_id))
                    conn.commit()
                    c.close()
                    conn.close()
                    st.balloons()
                    st.success(f"Tablier {t_id} marqué comme REMIS par {st.session_state['user_name']}.")
                    st.session_state["verified_ticket"] = None

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
        m2.metric("Tabliers Confirmés / Remis", f"{confirmes} / {total}")
        m3.metric("Reste total à encaisser", f"{restes_dus:.0f} DH")
        
        cols_base = [
            "ticket_id", "nom_prenom", "paiement", "reste", 
            "taille_standard", "epaules", "manche", "poitrine", "longueur", 
            "statut", "date_remise", "cree_par", "valide_par"
        ]
        available_cols = [col for col in cols_base if col in df.columns]
        df_display = df[available_cols].copy()
        
        col_names_fr = {
            "ticket_id": "Ticket",
            "nom_prenom": "Nom et Prénom",
            "paiement": "Paiement",
            "reste": "Reste (DH)",
            "taille_standard": "Taille Standard",
            "epaules": "Largeur épaules",
            "manche": "MANCHE",
            "poitrine": "POITRINE",
            "longueur": "Longueur",
            "statut": "Statut",
            "date_remise": "Date Remise",
            "cree_par": "Enregistré par (BDE)",
            "valide_par": "Validé par (BDE)"
        }
        df_display.rename(columns=col_names_fr, inplace=True)
        
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
