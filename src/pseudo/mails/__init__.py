"""Mails (.eml) : découpage, pseudonymisation du corps, des en-têtes et des pièces jointes, rangement
dans un dossier par mail.

    mail-jobs ──► split.handle_mail_job : lecture du .eml (parse.py), en-têtes + objet + noms des
                  pièces + corps pseudonymisés en un passage (document.py, html_body.py), pièces
                  jointes déposées dans work/ et envoyées comme tâches normales (pdf-jobs, image-jobs)
    pdf-jobs  ──► parts.handle_mail_part : pièce pseudonymisée, résultat écrit dans work/
    dernière pièce terminée ──► finalize.try_finalize : classement de l'ensemble, publication du
                  dossier pseudonymised/<type>/mail-…/ ou quarantine/<type>/mail-…/ (tout ou rien)
"""
