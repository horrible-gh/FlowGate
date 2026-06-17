import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

class MailUtil:
  # Email settings
    smtp_server = ""
    smtp_port = 0
    username = ""
    password = ""

    @staticmethod
    def init_mail(smtp_server, smtp_port, username, password):
        MailUtil.smtp_server = smtp_server
        MailUtil.smtp_port = smtp_port
        MailUtil.username = username
        MailUtil.password = password

    @staticmethod
    def send_mail(sender, receiver, subject, body_text, localhost_mode = False):
    # Compose email message
        msg = MIMEMultipart()
        msg['From'] = sender
        msg['To'] = receiver
        msg['Subject'] = subject
        msg.attach(MIMEText(body_text, 'plain'))

    # Connect to SMTP server and send email
        try:
            server = smtplib.SMTP(MailUtil.smtp_server, MailUtil.smtp_port)
            #server.set_debuglevel(1)  # Enable debug output
            if localhost_mode:
                server.ehlo('localhost')
            server.starttls()
            if localhost_mode:
                server.ehlo('localhost')
            server.login(MailUtil.username, MailUtil.password)
            server.sendmail(sender, receiver, msg.as_string())
            server.quit()
            return None
        except Exception as e:
            return e

# Usage example
#MailUtil.init_mail('smtp.example.com', 587, 'username', 'password')
#MailUtil.send_mail('sender@example.com', 'receiver@example.com', 'Test Subject', 'Test Body')
