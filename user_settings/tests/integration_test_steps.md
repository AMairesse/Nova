# Password Change Feature - Integration Test Steps

## Manual Testing Steps

### 1. Access Settings Dashboard
- Log in to Nova application
- Navigate to user settings (typically `/settings/` or similar)
- Click on the "General" tab

### 2. Verify Password Change Form
- Confirm "Change Password" section is visible
- Verify form contains:
  - Current password field
  - New password field
  - Confirm new password field
  - "Change Password" button

### 3. Test Successful Password Change
- Enter current password correctly
- Enter new password (meeting Django's password requirements)
- Confirm new password matches
- Click "Change Password"
- Expected: Success alert appears: "Password changed successfully!"
- Verify: User remains logged in (no logout occurred)
- Verify: Can log in with new password on fresh session

### 4. Test Error Scenarios
- **Wrong current password:**
  - Enter incorrect current password
  - Expected: Form reappears with error message on current password field

- **Password mismatch:**
  - Enter valid current password
  - Enter different values in new password fields
  - Expected: Form reappears with error message on confirm field

- **Weak password:**
  - Enter password not meeting requirements
  - Expected: Form reappears with validation errors

### 5. Test HTMX Behavior
- Submit form and observe no full page reload
- Success alert should appear without refreshing the page
- Error states should update the form inline

### 6. Test Session Persistence
- Change password successfully
- Continue using the application without interruption
- Verify no unexpected logout occurs

## Automated Test Commands

```bash
# Run unit tests
python manage.py test user_settings.tests.test_views --settings=nova.settings_test

# Run all user_settings tests
python manage.py test user_settings --settings=nova.settings_test
```

## Edge Cases to Test

1. **Long passwords:** Test with maximum allowed password length
2. **Special characters:** Test passwords with various special characters
3. **Unicode characters:** Test with non-ASCII characters if supported
4. **Rapid submissions:** Test multiple quick submissions (should be throttled if implemented)
5. **Browser back/forward:** Ensure form state is handled correctly
6. **Mobile responsiveness:** Test on mobile devices