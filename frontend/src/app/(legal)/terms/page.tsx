import type { Metadata } from "next";
import { LegalDocument, LegalSection } from "@/components/legal";

/**
 * `robots` is set here on purpose, overriding the root layout.
 *
 * `src/app/layout.tsx` sends `noindex, nofollow` for the whole app because
 * nothing in an approval queue should turn up in a search result. These two
 * pages are the exception the rule was never about: they are published so that
 * anyone - a platform reviewer, a team member, a person whose account is
 * connected - can read them without an account, and a public document that
 * refuses to be found is only half published.
 */
export const metadata: Metadata = {
  /*
   * Just the document's own name. The root layout's title template
   * (`%s | MeoChat`) appends the product, so the tab reads
   * "Terms of Service | MeoChat" - spelling the product out here as well would
   * compose to "Terms of Service | MeoChat | MeoChat".
   */
  title: "Terms of Service",
  description:
    "The terms that apply to using MeoChat, an internal tool for managing social media channels, reviewing content and monitoring performance.",
  robots: { index: true, follow: true },
};

export default function TermsPage() {
  return (
    <LegalDocument
      title="Terms of Service"
      lead="These Terms govern your use of MeoChat. By accessing MeoChat you agree to them. If you do not agree, please do not use the service."
      sibling={{ href: "/privacy", label: "Privacy Policy" }}
    >
      <LegalSection id="about" heading="1. About MeoChat">
        <p>
          MeoChat is a web-based internal communication and social media management tool used by a
          public relations and communications team. It helps authorized team members manage the
          social media channels their organization operates, review and approve content before it is
          published, and monitor the performance of accounts and posts.
        </p>
        <p>
          MeoChat is provided for internal, work-related use by the organization that operates it and
          the people that organization grants access to. It is not a consumer product and it is not
          offered to the general public.
        </p>
      </LegalSection>

      <LegalSection id="eligibility" heading="2. Eligibility and authorized use">
        <p>
          You may use MeoChat only if an administrator of the organization that operates it has
          granted you access, and only for the purposes that access was granted for. You must be old
          enough to enter into a binding agreement in the place where you live, and you must have
          the authority to act on behalf of the organization or accounts you use MeoChat with.
        </p>
        <p>
          You must only connect social media accounts you own or are authorized to manage. If your
          authority over a connected account ends, you are responsible for disconnecting it from
          MeoChat.
        </p>
      </LegalSection>

      <LegalSection id="accounts" heading="3. Accounts and access">
        <p>
          Access to MeoChat is issued to a person, not shared. You are responsible for keeping your
          access to your account secure, for everything done through it, and for telling an
          administrator promptly if you believe someone else has gained access to it.
        </p>
        <p>
          Different people are given different permissions inside MeoChat. Attempting to obtain or use
          access or data beyond what has been granted to you is a breach of these Terms.
        </p>
      </LegalSection>

      <LegalSection id="acceptable-use" heading="4. Acceptable use">
        <p>When using MeoChat, you agree not to:</p>
        <ul>
          <li>use it for any unlawful purpose, or in breach of any applicable law or regulation;</li>
          <li>
            connect, or attempt to connect, a social media account you are not authorized to manage;
          </li>
          <li>
            access, copy or share data belonging to another person or another organization without
            authorization;
          </li>
          <li>
            attempt to bypass authentication, permission checks, rate limits or any other technical
            control;
          </li>
          <li>
            interfere with the operation of the service, including by probing, scanning or
            overloading it;
          </li>
          <li>
            use MeoChat in a way that breaches the terms of any third-party platform connected to it;
          </li>
          <li>
            reverse engineer, resell or otherwise make the service available to people outside the
            organization it was provided to.
          </li>
        </ul>
      </LegalSection>

      <LegalSection id="third-party" heading="5. Third-party services and social platforms">
        <p>
          MeoChat connects to third-party platforms so that authorized accounts can be managed and
          measured from one place. Those platforms are operated by other companies. They are not
          part of MeoChat, and your use of them is governed by their own terms of service, developer
          policies and privacy policies, which apply in addition to these Terms.
        </p>
        <p>
          What MeoChat can read or show for a connected account depends entirely on what that platform
          permits and on the permissions you grant when you authorize the connection. A platform may
          change its APIs, its policies or its available data at any time, which may change or remove
          functionality in MeoChat without notice. MeoChat is not responsible for the availability,
          accuracy or behaviour of third-party platforms.
        </p>
      </LegalSection>

      <LegalSection id="tiktok" heading="6. TikTok integration">
        <p>
          MeoChat is an independent tool. It is <strong>not</strong> affiliated with, endorsed by,
          sponsored by or otherwise associated with TikTok or its affiliates. &ldquo;TikTok&rdquo; is
          used here only to identify the platform MeoChat can connect to.
        </p>
        <p>
          MeoChat uses TikTok Login Kit and the TikTok Display API. A TikTok account is connected only
          when you complete TikTok&rsquo;s own authorization screen and grant the permissions
          requested there. MeoChat then reads, within those granted permissions only, basic profile
          information, profile details, account statistics and the account&rsquo;s own videos and
          their available metadata, so that the connected channel can be displayed and measured
          inside MeoChat.
        </p>
        <p>
          MeoChat does not post, upload or publish to TikTok, does not read TikTok direct messages,
          and does not access anything beyond the permissions you grant. Your use of TikTok remains
          governed by TikTok&rsquo;s Terms of Service and its other policies. You can disconnect a
          TikTok account from MeoChat at any time, and you can also review or remove app access from
          within TikTok. See the <a href="/privacy">Privacy Policy</a> for what is collected and how
          long it is kept.
        </p>
      </LegalSection>

      <LegalSection id="content" heading="7. Your content and social media data">
        <p>
          You and your organization keep ownership of the content you create in MeoChat and of the
          data belonging to the social media accounts you connect. MeoChat does not claim ownership of
          any of it.
        </p>
        <p>
          You grant MeoChat permission to store, process and display that content and data for the
          sole purpose of operating the service for you: showing drafts to the reviewers who need to
          approve them, keeping a record of approvals and changes, and presenting the metrics read
          from connected platforms.
        </p>
        <p>
          You are responsible for the content you submit, including that you have the rights to use
          it and that it complies with the rules of any platform it is intended for.
        </p>
      </LegalSection>

      <LegalSection id="availability" heading="8. Availability and changes to the service">
        <p>
          MeoChat is provided on an as-available basis. It may be unavailable during maintenance, and
          it may be interrupted by faults, by network problems, or by changes at the third-party
          platforms it depends on. No specific level of availability is promised.
        </p>
        <p>
          Features may be added, changed or removed as the service develops or as a connected
          platform changes what it allows. Where a change materially affects how you use MeoChat, the
          organization operating it will try to give reasonable notice.
        </p>
      </LegalSection>

      <LegalSection id="warranties" heading="9. Disclaimer of warranties">
        <p>
          To the extent permitted by law, MeoChat is provided &ldquo;as is&rdquo; and &ldquo;as
          available&rdquo;, without warranties of any kind, whether express or implied, including
          implied warranties of merchantability, fitness for a particular purpose and
          non-infringement.
        </p>
        <p>
          In particular, figures shown in MeoChat are read from third-party platforms and are only as
          accurate, current and complete as those platforms make them. They should not be treated as
          an authoritative record, and MeoChat does not warrant that the service will be uninterrupted
          or error-free.
        </p>
      </LegalSection>

      <LegalSection id="liability" heading="10. Limitation of liability">
        <p>
          To the extent permitted by law, MeoChat and the organization operating it are not liable for
          indirect, incidental, special or consequential damages, or for loss of profits, revenue,
          goodwill or data, arising out of or relating to your use of the service.
        </p>
        <p>
          Nothing in these Terms limits liability that cannot be limited by law. Some jurisdictions
          do not allow certain exclusions, in which case the exclusions above apply only as far as
          that jurisdiction permits.
        </p>
      </LegalSection>

      <LegalSection id="termination" heading="11. Suspension and termination of access">
        <p>
          Your access to MeoChat may be suspended or ended at any time by an administrator of the
          organization that provided it — for example when you change role, when you leave the
          organization, or when these Terms have been breached. Where circumstances allow, notice
          will be given.
        </p>
        <p>
          You may stop using MeoChat at any time and ask an administrator to remove your access. When
          access ends, connected accounts should be disconnected; see &ldquo;Data deletion&rdquo; in
          the <a href="/privacy">Privacy Policy</a> for what happens to data afterwards.
        </p>
      </LegalSection>

      <LegalSection id="changes" heading="12. Changes to these Terms">
        <p>
          These Terms may be updated as the service changes. When they are, the effective date at the
          top of this page is updated, and the current version is always the one published here.
          Continuing to use MeoChat after a change means you accept the updated Terms.
        </p>
      </LegalSection>

      <LegalSection id="contact" heading="13. Contact">
        <p>
          For questions about these Terms, contact the administrator who provided your MeoChat access.
        </p>
      </LegalSection>
    </LegalDocument>
  );
}
