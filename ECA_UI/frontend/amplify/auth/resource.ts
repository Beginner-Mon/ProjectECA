import { defineAuth, secret } from '@aws-amplify/backend';
import { OAUTH_CALLBACK_URLS, OAUTH_LOGOUT_URLS } from '../shared/origins';

export const auth = defineAuth({
  loginWith: {
    email: true,
    externalProviders: {
      google: {
        clientId: secret('GOOGLE_CLIENT_ID'),
        clientSecret: secret('GOOGLE_CLIENT_SECRET'),
        scopes: ['email', 'profile', 'openid'],
        attributeMapping: {
          email: 'email',
          emailVerified: 'email_verified',
          givenName: 'given_name',
          familyName: 'family_name',
        }
      },
      // Includes the `ecaapp://callback/` custom scheme so the system browser
      // can hand control back to an installed Capacitor build. Cognito accepts
      // custom schemes; the same URI must also be registered in the Google
      // console and declared by the native project.
      callbackUrls: OAUTH_CALLBACK_URLS,
      logoutUrls: OAUTH_LOGOUT_URLS,
    }
  },
  userAttributes: {
    preferredUsername: { required: false, mutable: true },
    givenName: { required: false, mutable: true },
    familyName: { required: false, mutable: true },
  },
  // Who may manage tiers and quotas. Membership is set in the Cognito console
  // or CLI only — no app route can add anyone to this group, so one stolen
  // admin login cannot mint more admins. The backend reads it from the ID
  // token's `cognito:groups` claim (agenticRAG/langgraph_agents/api/auth.py).
  groups: ['admin'],
});
