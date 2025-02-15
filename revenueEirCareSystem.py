#!/usr/bin/env python3

"""
eirCare Multi-Plan Financial Projection Tool for Skyward Prompted
=========================================================
This script allows you to define multiple plan groups (clinics, hospitals, or provider-based accounts),
each with its own:
  - Plan type ("clinic", "hospital", "provider")
  - Number of accounts
  - Providers per account (for 'provider' plan, or to confirm for clinics/hospitals)
  - Usage volumes (messages, voice mins, etc.) per account

We then:
  1) Calculate monthly or quarterly fees for each group,
  2) Sum them up at each billing period,
  3) Add global milestone-based dev fees (once total, not per group),
  4) Compare the total to the minimum revenue guarantee after the final month.

Author: Breyden Taylor / Skyward Prompted
"""

################################
# Imports
################################
import math

################################
# CONSTANTS / GLOBALS
################################

# Minimum revenue guarantee details
MIN_REVENUE_GUARANTEE = 500_000.0
ONE_TIME_ADJUSTMENT_FEE = 100_000.0

# Paid development milestones (example structure)
MILESTONE_SCHEDULE = {
    1: 18_000.0,  # 30% upon signing (month 1)
    3: 24_000.0,  # 40% upon test environment integration (assume month 3)
    6: 18_000.0,  # 30% upon live EHR deployment (assume month 6)
}

################################
# CLASSES
################################

class MilestonePayment:
    """
    Tracks milestone-based payments for the short-term development agreement.
    If a month matches a defined milestone, that payment is incurred exactly once.
    """

    def __init__(self, schedule_dict: dict):
        """
        schedule_dict is a dictionary of {month_number: amount}.
        Example: {1: 18000, 3: 24000, 6: 18000}.
        """
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
    If monthly, you collect every month. If quarterly, you collect at months 3,6,9,12, etc.
    """

    def __init__(self, cycle_type="monthly"):
        """
        cycle_type: "monthly" or "quarterly"
        """
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


################################
# DATA STRUCTURE FOR MULTIPLE GROUPS
################################

class AccountGroup:
    """
    Represents a group of accounts that all share:
      - A plan type ("clinic", "hospital", or "provider")
      - The same # of providers (for 'provider' plan) or at least a typical # for that account
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

    def __repr__(self):
        return (f"<AccountGroup plan={self.plan_type}, count={self.accounts_count}, "
                f"providers={self.providers_per_account}>")

################################
# MAIN PROJECTION LOGIC
################################

class FinancialProjection:
    """
    Orchestrates the entire calculation for multiple account groups plus global dev milestones.
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
        Returns the total projected revenue after self.total_months, 
        while printing monthly (or quarterly) details.
        """

        total_revenue = 0.0
        billing_months = self.billing_manager.get_billing_points(self.total_months)

        print("\n================= MULTI-PLAN REVENUE PROJECTION =================\n")
        for month in range(1, self.total_months + 1):
            # 1) Collect milestone (if any) for this month
            milestone_payment = self.milestone_payments.get_payment_for_month(month)
            month_revenue = milestone_payment

            # 2) If it's a billing month, sum up the platform + usage fees across all groups
            if month in billing_months:
                # We'll accumulate the total monthly fees from all account groups
                month_platform_total = 0.0
                month_usage_total = 0.0

                for group in account_groups:
                    # Calculate the monthly platform fee for ONE account in this group
                    fee_for_one_account = self.platform_calculator.compute_monthly_fee(
                        group.plan_type,
                        group.providers_per_account
                    )
                    # Multiply by how many accounts are in this group
                    group_platform = fee_for_one_account * group.accounts_count

                    # Usage cost for ONE account in this group
                    usage_for_one = self.usage_fees.compute_monthly_cost(
                        monthly_messages=group.monthly_messages,
                        monthly_voice_minutes=group.monthly_voice_minutes,
                        monthly_appointments=group.monthly_appointments,
                        monthly_refills=group.monthly_refills,
                        monthly_flagged=group.monthly_flagged,
                        monthly_outreach=group.monthly_outreach
                    )
                    group_usage = usage_for_one * group.accounts_count

                    month_platform_total += group_platform
                    month_usage_total += group_usage

                # If billing quarterly, multiply by 3 to represent 3 months of usage & platform fees.
                if self.billing_manager.cycle_type == "quarterly":
                    month_platform_total *= 3
                    month_usage_total *= 3

                month_revenue += (month_platform_total + month_usage_total)

            total_revenue += month_revenue

            # Print monthly breakdown
            if month in billing_months:
                print(f"Month {month:2d}: MILESTONE=${milestone_payment:,.2f}, "
                      f"PLATFORM+USAGE=${month_revenue - milestone_payment:,.2f}, "
                      f"TOTAL=${month_revenue:,.2f}")
            else:
                # No platform/usage billing, only milestone (if any)
                print(f"Month {month:2d}: MILESTONE=${milestone_payment:,.2f}, "
                      f"(No platform/usage billed), "
                      f"TOTAL=${month_revenue:,.2f}")

        # 3) Check minimum revenue guarantee
        print(f"\nProjected Total Revenue after {self.total_months} months: ${total_revenue:,.2f}")
        if total_revenue < MIN_REVENUE_GUARANTEE:
            print("\n*** WARNING: Total projected revenue is below the minimum guarantee! ***")
            print(f"  - Projected: ${total_revenue:,.2f}")
            print(f"  - Minimum Guarantee: ${MIN_REVENUE_GUARANTEE:,.2f}")
            print(f"Skyward Prompted may renegotiate OR apply a one-time adjustment fee of "
                  f"${ONE_TIME_ADJUSTMENT_FEE:,.2f}.\n")
            # If you want to automatically add the fee, uncomment:
            # total_revenue += ONE_TIME_ADJUSTMENT_FEE
            # print(f"Revenue after adding one-time fee: ${total_revenue:,.2f}")

        return total_revenue


################################
# EXAMPLE USAGE / CLI
################################

def main():
    print("Welcome to the Multi-Plan Financial Projection Tool!\n")

    # Choose billing cycle
    billing_cycle = input("Choose billing cycle (monthly / quarterly) [monthly]: ") or "monthly"
    # Choose total months
    try:
        total_months = int(input("Projection length in months [12]: ") or 12)
    except ValueError:
        total_months = 12

    # We'll let the user specify how many plan groups to define
    try:
        group_count = int(input("How many plan groups do you want to define? [2]: ") or 2)
    except ValueError:
        group_count = 2

    # Build up the list of account groups
    account_groups = []
    for i in range(1, group_count + 1):
        print(f"\n--- Define Account Group #{i} ---")
        plan_type = input("Plan type? (clinic / hospital / provider) [provider]: ") or "provider"
        try:
            accounts_count = int(input(f"Number of accounts for this plan [1]: ") or 1)
        except ValueError:
            accounts_count = 1

        try:
            providers_per_acct = int(input("Providers per account [5]: ") or 5)
        except ValueError:
            providers_per_acct = 5

        # Usage volumes for one account
        try:
            monthly_messages = int(input("Monthly chat messages per account [500]: ") or 500)
            monthly_voice_minutes = int(input("Monthly voice minutes per account [50]: ") or 50)
            monthly_appointments = int(input("Monthly appointments per account [20]: ") or 20)
            monthly_refills = int(input("Monthly prescription refills per account [10]: ") or 10)
            monthly_flagged = int(input("Monthly no-show risk flags per account [5]: ") or 5)
            monthly_outreach = int(input("Monthly outreach messages per account [25]: ") or 25)
        except ValueError:
            monthly_messages = 500
            monthly_voice_minutes = 50
            monthly_appointments = 20
            monthly_refills = 10
            monthly_flagged = 5
            monthly_outreach = 25

        group = AccountGroup(
            plan_type=plan_type,
            accounts_count=accounts_count,
            providers_per_account=providers_per_acct,
            monthly_messages=monthly_messages,
            monthly_voice_minutes=monthly_voice_minutes,
            monthly_appointments=monthly_appointments,
            monthly_refills=monthly_refills,
            monthly_flagged=monthly_flagged,
            monthly_outreach=monthly_outreach
        )
        account_groups.append(group)

    # Now run the projection
    projection = FinancialProjection(
        milestone_schedule=MILESTONE_SCHEDULE,
        usage_fees=UsageFees(),               # default usage cost
        platform_calculator=PlatformFeeCalculator(),
        billing_cycle=billing_cycle,
        total_months=total_months
    )

    total_revenue = projection.project_revenue(account_groups)
    print(f"\nFinal Projected Revenue: ${total_revenue:,.2f}\n")


if __name__ == "__main__":
    main()
