import type { Metadata } from "next";
import { LegalDocument, LegalSection } from "@/components/legal";

/** Indexable for the same reason as /terms - see the note in that file. */
export const metadata: Metadata = {
  // Composed to "Privacy Policy | TasksBot" by the root title template - see the
  // note in terms/page.tsx.
  title: "Privacy Policy",
  description:
    "How TasksBot collects, uses, stores and deletes account information and data from the social media platforms an authorized user connects, including TikTok.",
  robots: { index: true, follow: true },
};

export default function PrivacyPage() {
  return (
    <LegalDocument
      title="Privacy Policy"
      lead="This policy explains what information TasksBot collects, why, how it is used and stored, and how it can be deleted."
      sibling={{ href: "/terms", label: "Terms of Service" }}
    >
      <LegalSection id="overview" heading="1. Overview">
        <p>
          TasksBot is a web-based internal communication and social media management tool used by a
          public relations and communications team. It helps authorized team members manage the
          social media channels their organization operates, review content, and monitor
          performance.
        </p>
        <p>
          TasksBot collects only what it needs to do that. It does not sell personal information, does
          not use it for advertising, and does not share it with third parties for their own
          purposes.
        </p>
      </LegalSection>

      <LegalSection id="collect" heading="2. Information TasksBot may collect">
        <h3>Account and authentication information</h3>
        <p>
          To give you access, TasksBot stores the identity your administrator set up for you: your name
          as recorded in the workspace, your role and permissions, the messaging account used to send
          you a sign-in link, and a session record for the browser you are signed in on. Access to
          the web panel is granted through single-use sign-in links rather than a password you set,
          so TasksBot holds no password for your TasksBot account.
        </p>

        <h3>Connected social platform information</h3>
        <p>
          When an authorized user connects a social media account, TasksBot stores what identifies that
          connection — the platform, the account identifier and display name the platform returns,
          which permissions were granted, and the connection&rsquo;s status — together with the
          profile details and metrics the platform makes available for that account.
        </p>
        <p>
          TasksBot never receives or stores your password for a connected platform. Connections are
          made through the platform&rsquo;s own authorization flow, which happens on the
          platform&rsquo;s site.
        </p>

        <h3>TikTok data</h3>
        <p>
          TasksBot can access TikTok data <strong>only</strong> after you authorize the app through
          TikTok&rsquo;s OAuth screen, and <strong>only</strong> within the scopes you grant there.
          If a scope is not granted, TasksBot cannot read the data behind it. TasksBot currently requests
          four read-only scopes:
        </p>
        <ul>
          <li>
            <strong>user.info.basic</strong> — basic profile information: the account identifiers
            TikTok issues for the app, the display name and the avatar image. This is what identifies
            which account a connection belongs to.
          </li>
          <li>
            <strong>user.info.profile</strong> — further display and profile information: the
            username, the link to the profile, the bio text and whether the account is verified. This
            is what lets you confirm in TasksBot which TikTok account you authorized.
          </li>
          <li>
            <strong>user.info.stats</strong> — account statistics: follower count, following count,
            total likes and total number of videos.
          </li>
          <li>
            <strong>video.list</strong> — the account&rsquo;s own videos as made available through the
            TikTok Display API, together with the metadata and statistics TikTok exposes for them,
            such as title or description, creation time, duration, cover image, links, and view, like,
            comment and share counts.
          </li>
        </ul>
        <p>
          TasksBot does <strong>not</strong> collect your TikTok password, does{" "}
          <strong>not</strong> post, upload or publish anything to TikTok, does <strong>not</strong>{" "}
          read TikTok direct messages, and does <strong>not</strong> access any data beyond the
          scopes you grant.
        </p>
        <p>
          To keep an authorized connection working without asking you to sign in again for every
          read, TasksBot securely stores the access and refresh tokens TikTok issues. They are held
          encrypted, are used only to make the authorized read requests described above, and are
          never shown in the interface or shared with anyone.
        </p>

        <h3>Usage and technical information</h3>
        <p>
          Operating the service produces ordinary technical records: server and application logs of
          requests and errors, timestamps, and an audit trail of significant actions taken inside
          TasksBot — who created, edited, approved or published a piece of content, and when. These
          exist so the service can be run, secured and debugged, and so that an approval can be
          traced back to the person who gave it.
        </p>
      </LegalSection>

      <LegalSection id="use" heading="3. How information is used">
        <p>TasksBot uses the information above to:</p>
        <ul>
          <li>sign you in and keep your session active;</li>
          <li>apply the permissions your administrator granted you;</li>
          <li>
            run the content workflow — drafting, review, approval and the record of what happened;
          </li>
          <li>
            display the connected social media accounts and the statistics read from those platforms;
          </li>
          <li>send you notifications about work that needs your attention;</li>
          <li>keep the service secure, diagnose faults and prevent abuse;</li>
          <li>meet legal or regulatory obligations that apply to the organization operating it.</li>
        </ul>
        <p>
          Data obtained from TikTok is used only to provide the connected social media management and
          analytics functionality described in this policy. It is not used for advertising, not sold,
          and not repurposed for anything else.
        </p>
      </LegalSection>

      <LegalSection id="basis" heading="4. Why TasksBot is allowed to process this information">
        <p>In practical terms, TasksBot processes information because:</p>
        <ul>
          <li>
            <strong>you or your organization asked it to</strong> — you were given access as part of
            your work, and you explicitly authorized each connected social media account through that
            platform&rsquo;s own consent screen;
          </li>
          <li>
            <strong>it is necessary to provide the service</strong> — the panel cannot show a channel
            it is not allowed to read, or route an approval without knowing who may give it;
          </li>
          <li>
            <strong>there is a legitimate operational interest</strong> — keeping the service secure
            and available, and keeping an audit record of decisions made in it;
          </li>
          <li>
            <strong>the law sometimes requires it</strong> — where the organization operating TasksBot
            must retain or produce records.
          </li>
        </ul>
        <p>
          Where processing depends on your authorization, you can withdraw it by disconnecting the
          account concerned or by asking an administrator to remove your access.
        </p>
      </LegalSection>

      <LegalSection id="sharing" heading="5. Data sharing">
        <p>
          TasksBot does not sell personal information and does not share it with third parties for
          their own marketing or advertising.
        </p>
        <p>Information is visible or disclosed only:</p>
        <ul>
          <li>
            <strong>inside your workspace</strong> — to other authorized members of your team,
            according to the permissions they hold. Content, approvals and channel metrics are shared
            working material by design;
          </li>
          <li>
            <strong>to the connected platform</strong> — requests TasksBot makes on your behalf are
            necessarily sent to that platform;
          </li>
          <li>
            <strong>to service providers</strong> that host or support the deployment, as described
            below;
          </li>
          <li>
            <strong>where the law requires it</strong>, or where disclosure is necessary to
            investigate a security incident or protect the rights and safety of people involved.
          </li>
        </ul>
      </LegalSection>

      <LegalSection id="providers" heading="6. Third-party service providers">
        <p>
          TasksBot runs on infrastructure and services chosen by the organization that operates it —
          typically hosting and database services, and the messaging platform used to deliver sign-in
          links and notifications. Depending on the workspace&rsquo;s configuration, optional
          integrations such as file storage, spreadsheets or an AI assistant used to help draft or
          review text may also process content submitted to TasksBot.
        </p>
        <p>
          These providers process data on the organization&rsquo;s behalf, for the purpose of running
          TasksBot, and are subject to their own terms and privacy policies. Your administrator can tell
          you which are enabled in your workspace.
        </p>
      </LegalSection>

      <LegalSection id="retention" heading="7. Data retention">
        <p>
          Information is kept for as long as it is needed to run the service for your organization.
          In practice that means:
        </p>
        <ul>
          <li>account and permission records are kept while your access exists;</li>
          <li>
            sessions expire on their own, and sign-in links are single-use and valid for a few
            minutes;
          </li>
          <li>
            connection credentials for a social platform are kept while the connection is active, and
            are removed when it is disconnected;
          </li>
          <li>
            content, approvals and audit records are kept as the organization&rsquo;s working record
            of what was published and by whom;
          </li>
          <li>technical logs are kept for a limited period for security and troubleshooting.</li>
        </ul>
        <p>
          When data is no longer needed, or when a valid deletion request is acted on, it is deleted
          or anonymized. Backups may hold a copy for a short additional period before they rotate.
        </p>
      </LegalSection>

      <LegalSection id="security" heading="8. Security">
        <p>
          TasksBot is protected with measures appropriate to an internal tool that holds authorized
          platform credentials. Traffic is served over HTTPS; the session cookie is HTTP-only,
          same-site and inaccessible to page scripts; access to every action is checked on the server
          against the permissions you hold; and platform tokens are stored encrypted rather than in
          plain text.
        </p>
        <p>
          No system is completely secure. You help protect your data by keeping your sign-in link
          private, using it only in a browser you trust, and telling an administrator immediately if
          you suspect your access has been compromised.
        </p>
      </LegalSection>

      <LegalSection id="choices" heading="9. Your choices and disconnecting an account">
        <p>You can, at any time:</p>
        <ul>
          <li>sign out of the web panel;</li>
          <li>
            disconnect a connected social media account in TasksBot. Disconnecting removes the stored
            credential for that connection and stops future access through it. TasksBot also asks the
            platform to revoke the authorization where the platform supports it;
          </li>
          <li>
            revoke TasksBot&rsquo;s access from the platform&rsquo;s own settings — for TikTok, from the
            connected-apps section of your TikTok account;
          </li>
          <li>
            ask an administrator to correct information held about you, or to remove your access
            entirely.
          </li>
        </ul>
        <p>
          Disconnecting stops future access. Statistics already read and stored for that channel
          remain in TasksBot until they are deleted; see the next section.
        </p>
      </LegalSection>

      <LegalSection id="deletion" heading="10. Data deletion">
        <p>
          Users may request deletion of account-related or connected-platform data by contacting the
          TasksBot administrator. A request may cover your TasksBot account and access, the data stored
          for a specific connected social media account, or both.
        </p>
        <p>
          Requests are acted on within a reasonable period. Where a record must be kept for legal
          reasons or as part of the organization&rsquo;s audit trail, that will be explained rather
          than left unsaid, and the remainder will still be deleted.
        </p>
        <p>
          Deleting data from TasksBot does not delete anything from TikTok or any other platform. To
          remove content or data held by a platform, use that platform&rsquo;s own tools.
        </p>
      </LegalSection>

      <LegalSection id="international" heading="11. Third-party platforms and international transfers">
        <p>
          The platforms TasksBot connects to operate globally. When TasksBot makes an authorized request
          on your behalf, that request — and the data returned — necessarily travels to and from
          servers those platforms run, which may be in other countries. Any service providers used to
          host or support TasksBot may likewise operate in more than one country.
        </p>
        <p>
          Data held by a third-party platform stays governed by that platform&rsquo;s own privacy
          policy, which applies in addition to this one.
        </p>
      </LegalSection>

      <LegalSection id="children" heading="12. Children's privacy">
        <p>
          TasksBot is a workplace tool for authorized team members. It is not directed at children, and
          it is not intended for anyone under the age required to use it or the connected platforms in
          their country. TasksBot does not knowingly collect information from children. If such
          information is found to have been collected, it will be deleted.
        </p>
      </LegalSection>

      <LegalSection id="changes" heading="13. Changes to this Privacy Policy">
        <p>
          This policy may be updated as the service changes or as connected platforms change what
          they make available. When it is, the effective date at the top of this page is updated, and
          the current version is always the one published here. Where a change materially affects how
          your information is handled, the organization operating TasksBot will try to give reasonable
          notice.
        </p>
      </LegalSection>

      <LegalSection id="contact" heading="14. Contact">
        <p>
          For privacy or data deletion requests, contact the administrator who provided your TasksBot
          access. See also the <a href="/terms">Terms of Service</a>.
        </p>
      </LegalSection>
    </LegalDocument>
  );
}
