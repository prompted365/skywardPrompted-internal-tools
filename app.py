#!/usr/bin/env python3

import streamlit as st

########################################################
# ALL CONSTANTS & CLASS DEFINITIONS
########################################################

# 1. Global contract constants
MIN_REVENUE_GUARANTEE = 500_000.0
ONE_TIME_ADJUSTMENT_FEE = 100_000.0

MILESTONE_SCHEDULE = {
    1: 18_000.0,  # 30% upon signing
    3: 24_000.0,  # 40% after integration
    6: 18_000.0,  # 30% after live EHR
}

class MilestonePayment:
    """
    Tracks milestone-based payments for the short-term development agreement.
    If a month matches a defined milestone, that payment is incurred exactly once.
    """
    def __init__(self, schedule_dict: dict):
        self.schedule = schedule_dict
        self.paid_milestones = set()

    def get_payment_for_month(self, month: int) -> float:
        """
        Returns the milestone payment for this month, if any.
        Ensures each milestone is only paid once.
        """
        if month in self.schedule and month not in self.paid_milestones:
            self.paid_milestones.add(month)
            return self.schedule[month]
        return 0.0

class UsageFees:
    """
    Encapsulates usage-based fees for AI services:
      - Chat messages, voice minutes, appointments, refills, flagged patients, outreach.
    """
    def __init__(self,
                 cost_per_message=0.02,
                 cost_per_voice_minute=0.20,
                 cost_per_appointment=1.00,
                 cost_per_refill=0.75,
                 cost_per_flagged=1.00,
                 cost_per_outreach=0.25):
        self.cost_per_message = cost_per_message
        self.cost_per_voice_minute = cost_per_voice_minute
        self.cost_per_appointment = cost_per_appointment
        self.cost_per_refill = cost_per_refill
        self.cost_per_flagged = cost_per_flagged
        self.cost_per_outreach = cost_per_outreach

    def compute_monthly_cost(self,
                             monthly_messages=0,
                             monthly_voice_minutes=0,
                             monthly_appointments=0,
                             monthly_refills=0,
                             monthly_flagged=0,
                             monthly_outreach=0) -> float:
        """
        Calculates total usage cost for one month for a *single* account.
        """
        return (
            monthly_messages * self.cost_per_message +
            monthly_voice_minutes * self.cost_per_voice_minute +
            monthly_appointments * self.cost_per_appointment +
            monthly_refills * self.cost_per_refill +
            monthly_flagged * self.cost_per_flagged +
            monthly_outreach * self.cost_per_outreach
        )

class PlatformFeeCalculator:
    """
    Determines the base platform fee (monthly) based on plan type and number of providers.
    For 'clinic' or 'hospital' we do a flat rate. For 'provider' we do volume-based discount.
    """
    def compute_monthly_fee(self, plan_type: str, num_providers: int) -> float:
        """
        Returns the base platform fee (monthly) for one *account*.
        """
        plan_type = plan_type.lower()
        if plan_type == "clinic":
            # 1-5 providers => $150 flat
            return 150.0
        elif plan_type == "hospital":
            # 6+ providers => $400 flat
            return 400.0
        elif plan_type == "provider":
            # Volume-based discount approach:
            #  0-50  => $30/provider
            #  51-200 => $27/provider
            #  201+   => $25/provider
            if num_providers <= 50:
                return num_providers * 30.0
            elif num_providers <= 200:
                return num_providers * 27.0
            else:
                return num_providers * 25.0
        else:
            raise ValueError(f"Unsupported plan_type: {plan_type}")

class BillingCycleManager:
    """
    Manages whether billing occurs monthly or quarterly.
    If monthly, you collect every month. If quarterly, months 3,6,9,12, etc.
    """
    def __init__(self, cycle_type="monthly"):
        self.cycle_type = cycle_type.lower()

    def get_billing_points(self, total_months=12):
        """
        Returns the list of months on which billing occurs.
        If monthly, that's [1..total_months].
        If quarterly, that's [3, 6, 9, 12] for a 12-month horizon, etc.
        """
        if self.cycle_type == "monthly":
            return list(range(1, total_months + 1))
        elif self.cycle_type == "quarterly":
            return [m for m in range(1, total_months + 1) if m % 3 == 0]
        else:
            raise ValueError(f"Unsupported billing cycle: {self.cycle_type}")

class AccountGroup:
    """
    Represents a group of accounts that all share:
      - A plan type ("clinic", "hospital", or "provider")
      - The same # of providers (for 'provider' plan) or typical # for the plan
      - The same monthly usage assumptions
      - A certain number of total accounts in this group.
    """
    def __init__(self,
                 plan_type: str,
                 accounts_count: int,
                 providers_per_account: int,
                 monthly_messages=0,
                 monthly_voice_minutes=0,
                 monthly_appointments=0,
                 monthly_refills=0,
                 monthly_flagged=0,
                 monthly_outreach=0):
        self.plan_type = plan_type
        self.accounts_count = accounts_count
        self.providers_per_account = providers_per_account
        self.monthly_messages = monthly_messages
        self.monthly_voice_minutes = monthly_voice_minutes
        self.monthly_appointments = monthly_appointments
        self.monthly_refills = monthly_refills
        self.monthly_flagged = monthly_flagged
        self.monthly_outreach = monthly_outreach

class FinancialProjection:
    """
    Orchestrates the entire calculation for multiple account groups + dev milestones.
    """
    def __init__(self,
                 milestone_schedule=MILESTONE_SCHEDULE,
                 usage_fees=UsageFees(),
                 platform_calculator=PlatformFeeCalculator(),
                 billing_cycle="monthly",
                 total_months=12):
        self.milestone_payments = MilestonePayment(milestone_schedule)
        self.usage_fees = usage_fees
        self.platform_calculator = platform_calculator
        self.billing_manager = BillingCycleManager(billing_cycle)
        self.total_months = total_months

    def project_revenue(self, account_groups):
        """
        account_groups: list of AccountGroup objects
        Returns:
          - total_revenue (float)
          - monthly_details (list of dicts)
          - under_minimum (bool) = True if below MIN_REVENUE_GUARANTEE
        """
        total_revenue = 0.0
        billing_months = self.billing_manager.get_billing_points(self.total_months)
        monthly_details = []

        for month in range(1, self.total_months + 1):
            # 1) Milestone payment for this month (if any)
            milestone_payment = self.milestone_payments.get_payment_for_month(month)
            month_revenue = milestone_payment
            platform_plus_usage = 0.0

            # 2) If it's a billing month, sum all account groups
            if month in billing_months:
                platform_total = 0.0
                usage_total = 0.0

                for group in account_groups:
                    # One account's platform fee
                    fee_for_one_account = self.platform_calculator.compute_monthly_fee(
                        group.plan_type,
                        group.providers_per_account
                    )
                    group_platform = fee_for_one_account * group.accounts_count

                    # One account's usage
                    usage_for_one = self.usage_fees.compute_monthly_cost(
                        monthly_messages=group.monthly_messages,
                        monthly_voice_minutes=group.monthly_voice_minutes,
                        monthly_appointments=group.monthly_appointments,
                        monthly_refills=group.monthly_refills,
                        monthly_flagged=group.monthly_flagged,
                        monthly_outreach=group.monthly_outreach
                    )
                    group_usage = usage_for_one * group.accounts_count

                    platform_total += group_platform
                    usage_total += group_usage

                # If quarterly billing, gather 3 months' worth at once
                if self.billing_manager.cycle_type == "quarterly":
                    platform_total *= 3
                    usage_total *= 3

                platform_plus_usage = platform_total + usage_total
                month_revenue += platform_plus_usage

            total_revenue += month_revenue

            monthly_details.append({
                "month": month,
                "milestone": milestone_payment,
                "platform_usage": platform_plus_usage,
                "month_total": month_revenue
            })

        under_minimum = (total_revenue < MIN_REVENUE_GUARANTEE)
        return total_revenue, monthly_details, under_minimum

########################################################
# STREAMLIT UI
########################################################

def main():
    st.title("eirCare - Skyward Prompted Financial Projection Tool")

    # Global parameters
    billing_cycle = st.selectbox("Billing Cycle:", ["monthly", "quarterly"])
    total_months = st.number_input("Projection length (months):", min_value=1, value=12)
    group_count = st.number_input("How many plan groups?", min_value=1, value=2)

    st.write("""
    Each "plan group" corresponds to a batch of accounts all using:
      - The same plan type (clinic, hospital, or provider)
      - The same usage assumptions (messages, minutes, etc.)
    """)

    account_groups = []
    for i in range(group_count):
        st.subheader(f"Plan Group #{i+1}")

        col1, col2 = st.columns(2)
        with col1:
            plan_type = st.selectbox(f"Plan Type (Group #{i+1})",
                                     ["provider", "clinic", "hospital"],
                                     key=f"plan_type_{i}")
            accounts_count = st.number_input(f"Number of accounts (Group #{i+1})",
                                             min_value=1, value=1,
                                             key=f"accounts_count_{i}")
        with col2:
            providers_per_acct = st.number_input(f"Providers per account (Group #{i+1})",
                                                 min_value=0, value=5,
                                                 key=f"providers_{i}")

        st.markdown("*Monthly Usage per Account*:")
        col3, col4, col5 = st.columns(3)
        with col3:
            monthly_messages = st.number_input(f"Chat messages (G#{i+1})", min_value=0, value=500,
                                               key=f"msg_{i}")
            monthly_voice_minutes = st.number_input(f"Voice mins (G#{i+1})", min_value=0, value=50,
                                                    key=f"voice_{i}")
        with col4:
            monthly_appointments = st.number_input(f"Appointments (G#{i+1})", min_value=0, value=20,
                                                   key=f"appt_{i}")
            monthly_refills = st.number_input(f"Refills (G#{i+1})", min_value=0, value=10,
                                              key=f"refills_{i}")
        with col5:
            monthly_flagged = st.number_input(f"Flagged (G#{i+1})", min_value=0, value=5,
                                              key=f"flagged_{i}")
            monthly_outreach = st.number_input(f"Outreach (G#{i+1})", min_value=0, value=25,
                                               key=f"outreach_{i}")

        group = AccountGroup(
            plan_type,
            accounts_count,
            providers_per_acct,
            monthly_messages,
            monthly_voice_minutes,
            monthly_appointments,
            monthly_refills,
            monthly_flagged,
            monthly_outreach
        )
        account_groups.append(group)

    if st.button("Compute Projection"):
        # Build the projection object
        projection = FinancialProjection(
            milestone_schedule=MILESTONE_SCHEDULE,
            usage_fees=UsageFees(),
            platform_calculator=PlatformFeeCalculator(),
            billing_cycle=billing_cycle,
            total_months=total_months
        )

        total_revenue, monthly_details, under_minimum = projection.project_revenue(account_groups)

        st.subheader("Results:")
        st.write(f"**Total Revenue for {total_months} months:** "
                 f"${total_revenue:,.2f}")

        if under_minimum:
            st.error(
                f"Below the minimum revenue guarantee of ${MIN_REVENUE_GUARANTEE:,.2f}! "
                f"Potential one-time fee: ${ONE_TIME_ADJUSTMENT_FEE:,.2f}."
            )

        # Show month-by-month breakdown
        st.write("### Monthly/Quarterly Details")
        st.write("| Month | Milestone ($) | Platform+Usage ($) | Total This Month ($) |")
        st.write("|-------|---------------|---------------------|-----------------------|")
        for row in monthly_details:
            st.write(f"| {row['month']} "
                     f"| {row['milestone']:.2f} "
                     f"| {row['platform_usage']:.2f} "
                     f"| {row['month_total']:.2f} |")

if __name__ == "__main__":
    main()
